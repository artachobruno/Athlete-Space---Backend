"""Backfill script for pairing unpaired activities with planned sessions.

This script processes activities that are NOT paired with planned sessions and attempts
to pair them using the auto-pairing service. This is useful after fixing pairing logic
issues (e.g., type mismatches) or when new pairing rules are added.

Usage:
    From project root:
    python scripts/backfill_unpaired_activities.py [--no-dry-run] [--user-id USER_ID] [--days DAYS]

    Or as a module:
    python -m scripts.backfill_unpaired_activities [--no-dry-run] [--user-id USER_ID] [--days DAYS]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
    - Can filter by user_id and days (recent activities only)
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta, timezone
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

from app.db.models import Activity
from app.db.session import SessionLocal
from app.pairing.auto_pairing_service import try_auto_pair
from app.pairing.session_links import get_link_for_activity


def process_unpaired_activities(
    db: Session,
    user_id: str | None = None,
    days: int | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Process unpaired activities and attempt to pair them.

    Args:
        db: Database session
        user_id: Optional user ID to filter by
        days: Optional number of days to look back (if None, processes all)
        dry_run: If True, only log what would be done without making changes

    Returns:
        Dictionary with statistics about the processing
    """
    stats: dict[str, int] = {
        "activities_found": 0,
        "paired": 0,
        "failed": 0,
        "skipped": 0,
    }

    # Build query for all activities (we'll filter unpaired ones below)
    # Filter out activities with null user_id (invalid data that would cause pairing decision errors)
    query = select(Activity).where(
        Activity.user_id.isnot(None),
    )

    if user_id:
        query = query.where(Activity.user_id == user_id)

    if days:
        cutoff_date = datetime.now(UTC) - timedelta(days=days)
        query = query.where(Activity.starts_at >= cutoff_date)

    query = query.order_by(Activity.starts_at.desc())

    all_activities = list(db.scalars(query).all())
    
    # Schema v2: Filter to only unpaired activities (those without SessionLink)
    activities = []
    for activity in all_activities:
        link = get_link_for_activity(db, activity.id)
        if not link:
            activities.append(activity)
    
    stats["activities_found"] = len(activities)

    logger.info(
        f"Found {len(activities)} unpaired activities"
        f"{f' for user {user_id}' if user_id else ''}"
        f"{f' from last {days} days' if days else ''}"
    )

    for activity in activities:
        try:
            # Skip activities with null user_id (shouldn't happen due to query filter, but double-check)
            if not activity.user_id:
                logger.warning(f"Skipping activity {activity.id} - user_id is None")
                stats["skipped"] += 1
                continue

            activity_date = activity.starts_at.date() if activity.starts_at else None
            logger.debug(
                f"Processing activity {activity.id}: "
                f"sport={activity.sport}, date={activity_date}, "
                f"duration={activity.duration_seconds}s, "
                f"distance={activity.distance_meters}m"
            )

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would attempt to pair activity {activity.id} "
                    f"({activity.sport} on {activity_date}, "
                    f"duration={activity.duration_seconds}s, "
                    f"distance={activity.distance_meters}m)"
                )
                stats["skipped"] += 1
            else:
                # Attempt pairing
                try_auto_pair(activity=activity, session=db)
                db.commit()

                # Schema v2: Check if pairing succeeded by checking SessionLink
                db.refresh(activity)
                link = get_link_for_activity(db, activity.id)
                if link:
                    stats["paired"] += 1
                    logger.info(
                        f"✅ Paired activity {activity.id} with planned session {link.planned_session_id}"
                    )
                else:
                    stats["failed"] += 1
                    logger.debug(f"❌ Failed to pair activity {activity.id}")

        except Exception as e:
            stats["failed"] += 1
            logger.error(
                f"Error processing activity {activity.id}: {e}",
                exc_info=True,
            )
            db.rollback()

    return stats


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Backfill script to pair unpaired activities with planned sessions"
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
        default=None,
        help="Only process activities from the last N days (optional)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    logger.info("=" * 80)
    logger.info("Backfill Unpaired Activities Script")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if args.user_id:
        logger.info(f"Filter: user_id={args.user_id}")
    if args.days:
        logger.info(f"Filter: last {args.days} days")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        stats = process_unpaired_activities(
            db=db,
            user_id=args.user_id,
            days=args.days,
            dry_run=dry_run,
        )

        logger.info("=" * 80)
        logger.info("Summary:")
        logger.info(f"  Activities found: {stats['activities_found']}")
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
