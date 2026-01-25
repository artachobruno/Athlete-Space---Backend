"""Script to repair unpaired activities that should be paired with planned sessions.

This script identifies activities that appear to match planned sessions but aren't paired,
and attempts to re-pair them. Useful when pairings have been lost or broken.

Usage:
    python scripts/repair_unpaired_activities.py [--no-dry-run] [--user-id USER_ID] [--days DAYS]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.db.session import SessionLocal
from app.pairing.auto_pairing_service import try_auto_pair
from app.pairing.session_links import get_link_for_activity, get_link_for_planned


def find_candidate_pairs(
    db: Session,
    user_id: str | None = None,
    days: int | None = None,
) -> list[tuple[PlannedSession, Activity]]:
    """Find planned sessions and activities that appear to match but aren't paired.

    Args:
        db: Database session
        user_id: Optional user ID to filter by
        days: Optional number of days to look back

    Returns:
        List of (planned_session, activity) tuples that appear to match
    """
    candidates: list[tuple[PlannedSession, Activity]] = []

    # Get all unpaired planned sessions
    planned_query = select(PlannedSession).where(
        PlannedSession.status.notin_(["deleted", "skipped", "completed"]),
    )

    if user_id:
        planned_query = planned_query.where(PlannedSession.user_id == user_id)

    if days:
        cutoff_date = datetime.now(UTC) - timedelta(days=days)
        planned_query = planned_query.where(PlannedSession.starts_at >= cutoff_date)

    planned_sessions = list(db.scalars(planned_query).all())

    # Get all unpaired activities
    activities_query = select(Activity).where(Activity.user_id.isnot(None))

    if user_id:
        activities_query = activities_query.where(Activity.user_id == user_id)

    if days:
        cutoff_date = datetime.now(UTC) - timedelta(days=days)
        activities_query = activities_query.where(Activity.starts_at >= cutoff_date)

    all_activities = list(db.scalars(activities_query).all())

    # Filter to unpaired activities
    unpaired_activities = []
    for activity in all_activities:
        link = get_link_for_activity(db, activity.id)
        if not link:
            unpaired_activities.append(activity)

    logger.info(
        f"Found {len(planned_sessions)} unpaired planned sessions and {len(unpaired_activities)} unpaired activities"
    )

    # Find matches by date and type
    for planned in planned_sessions:
        # Skip if already paired
        link = get_link_for_planned(db, planned.id)
        if link:
            continue

        planned_date = planned.starts_at.date() if planned.starts_at else None
        if not planned_date:
            continue

        # Find activities on the same day with matching type
        for activity in unpaired_activities:
            activity_date = activity.starts_at.date() if activity.starts_at else None
            if not activity_date:
                continue

            if activity_date == planned_date:
                # Check if types match (simplified - just check sport)
                if activity.sport and planned.type:
                    # Normalize types for comparison
                    activity_sport = activity.sport.lower()
                    planned_type = planned.type.lower()

                    # Basic matching: exact match or both are "run"
                    if activity_sport == planned_type or (
                        activity_sport == "run" and planned_type in ["easy", "long", "threshold", "tempo", "interval"]
                    ):
                        # Check duration match (within 20% tolerance)
                        if planned.duration_minutes and activity.duration_seconds:
                            planned_duration_min = planned.duration_minutes
                            activity_duration_min = activity.duration_seconds / 60.0
                            diff_pct = abs(planned_duration_min - activity_duration_min) / planned_duration_min

                            if diff_pct <= 0.2:  # 20% tolerance
                                candidates.append((planned, activity))

    return candidates


def repair_pairings(
    db: Session,
    user_id: str | None = None,
    days: int | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Repair unpaired activities by attempting to pair them with planned sessions.

    Args:
        db: Database session
        user_id: Optional user ID to filter by
        days: Optional number of days to look back
        dry_run: If True, only log what would be done

    Returns:
        Dictionary with statistics
    """
    stats: dict[str, int] = {
        "candidates_found": 0,
        "paired": 0,
        "failed": 0,
    }

    candidates = find_candidate_pairs(db, user_id=user_id, days=days)
    stats["candidates_found"] = len(candidates)

    logger.info(f"Found {len(candidates)} candidate pairs to repair")

    for planned, activity in candidates:
        try:
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would attempt to pair planned session {planned.id} "
                    f"({planned.type} on {planned.starts_at.date()}, {planned.duration_minutes}min) "
                    f"with activity {activity.id} "
                    f"({activity.sport} on {activity.starts_at.date()}, {activity.duration_seconds/60:.1f}min)"
                )
            else:
                # Try pairing from activity side (more reliable)
                try_auto_pair(activity=activity, session=db)
                db.commit()

                # Check if pairing succeeded
                link = get_link_for_activity(db, activity.id)
                if link and link.planned_session_id == planned.id:
                    stats["paired"] += 1
                    logger.info(
                        f"✅ Successfully paired planned session {planned.id} with activity {activity.id}"
                    )
                else:
                    stats["failed"] += 1
                    logger.debug(f"❌ Failed to pair planned session {planned.id} with activity {activity.id}")

        except Exception as e:
            stats["failed"] += 1
            logger.error(
                f"Error pairing planned {planned.id} with activity {activity.id}: {e}",
                exc_info=True,
            )
            db.rollback()

    return stats


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Repair unpaired activities that should be paired with planned sessions"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the pairing (default: dry-run mode)",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Filter by specific user ID (optional)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only process activities from the last N days (default: 30)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    logger.info("=" * 80)
    logger.info("Repair Unpaired Activities Script")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if args.user_id:
        logger.info(f"Filter: user_id={args.user_id}")
    logger.info(f"Filter: last {args.days} days")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        stats = repair_pairings(
            db=db,
            user_id=args.user_id,
            days=args.days,
            dry_run=dry_run,
        )

        logger.info("=" * 80)
        logger.info("Summary:")
        logger.info(f"  Candidate pairs found: {stats['candidates_found']}")
        if dry_run:
            logger.info(f"  Would attempt to pair: {stats['candidates_found']}")
            logger.info("  (Run with --no-dry-run to actually pair)")
        else:
            logger.info(f"  Successfully paired: {stats['paired']}")
            logger.info(f"  Failed to pair: {stats['failed']}")
        logger.info("=" * 80)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
