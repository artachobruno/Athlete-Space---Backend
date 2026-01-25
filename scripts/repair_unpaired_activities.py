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


def get_unpaired_activities(
    db: Session,
    user_id: str | None = None,
    days: int | None = None,
) -> list[Activity]:
    """Get all unpaired activities.

    Args:
        db: Database session
        user_id: Optional user ID to filter by
        days: Optional number of days to look back

    Returns:
        List of unpaired activities
    """
    # Get all activities
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

    return unpaired_activities


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
        "activities_found": 0,
        "paired": 0,
        "failed": 0,
    }

    unpaired_activities = get_unpaired_activities(db, user_id=user_id, days=days)
    stats["activities_found"] = len(unpaired_activities)

    logger.info(f"Found {len(unpaired_activities)} unpaired activities to attempt pairing")

    for activity in unpaired_activities:
        try:
            activity_date = activity.starts_at.date() if activity.starts_at else None
            duration_str = f"{activity.duration_seconds/60:.1f}min" if activity.duration_seconds else "no duration"
            
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would attempt to pair activity {activity.id} "
                    f"({activity.sport} on {activity_date}, {duration_str})"
                )
            else:
                # Try pairing from activity side (uses auto-pairing service matching logic)
                # This will check date, type, and duration matching automatically
                try_auto_pair(activity=activity, session=db)
                db.commit()

                # Check if pairing succeeded (could be paired to any planned session)
                link = get_link_for_activity(db, activity.id)
                if link:
                    stats["paired"] += 1
                    logger.info(
                        f"✅ Successfully paired activity {activity.id} with planned session {link.planned_session_id}"
                    )
                else:
                    stats["failed"] += 1
                    logger.debug(
                        f"❌ Auto-pairing service did not pair activity {activity.id} "
                        f"(no matching planned session found)"
                    )

        except Exception as e:
            stats["failed"] += 1
            logger.error(
                f"Error attempting to pair activity {activity.id}: {e}",
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
        logger.info(f"  Unpaired activities found: {stats['activities_found']}")
        if dry_run:
            logger.info(f"  Would attempt to pair: {stats['activities_found']}")
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
