"""Backfill script to rename generic Strava activity titles.

This script processes ALL activities with generic Strava titles like:
- Morning Run, Lunch Run, Afternoon Run, Evening Run, Night Run
- Morning Ride, Lunch Ride, etc.

And renames them based on activity metrics (distance, duration) to more descriptive titles.

Usage:
    From project root:
    python scripts/backfill_activity_titles.py [--no-dry-run] [--limit N]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
"""

from __future__ import annotations

import argparse
import sys
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

from app.db.models import Activity
from app.db.session import SessionLocal
from app.utils.title_utils import is_generic_strava_title, normalize_activity_title


def backfill_activity_titles(
    dry_run: bool = True,
    limit: int = 0,
) -> dict[str, int]:
    """Backfill generic Strava titles for all activities.

    Args:
        dry_run: If True, only log what would be done (default: True)
        limit: Maximum number of activities to process (0 = no limit)

    Returns:
        Dictionary with counts of items processed
    """
    stats: dict[str, int] = {
        "activities_found": 0,
        "titles_updated": 0,
        "already_good": 0,
        "errors": 0,
    }

    db = SessionLocal()
    try:
        logger.info(f"Starting activity title backfill (dry_run={dry_run}, limit={limit})")

        # Find all activities
        query = select(Activity).order_by(Activity.starts_at.desc())
        if limit > 0:
            query = query.limit(limit)

        activities = db.execute(query).scalars().all()

        logger.info(f"Found {len(activities)} activities to check")
        stats["activities_found"] = len(activities)

        for i, activity in enumerate(activities):
            try:
                if not is_generic_strava_title(activity.title):
                    stats["already_good"] += 1
                    continue

                new_title = normalize_activity_title(
                    strava_title=activity.title,
                    sport=activity.sport or "run",
                    distance_meters=activity.distance_meters,
                    duration_seconds=activity.duration_seconds,
                )

                # Don't update if title would be the same
                if new_title.lower() == (activity.title or "").lower():
                    stats["already_good"] += 1
                    continue

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Activity {activity.id}: "
                        f"'{activity.title}' -> '{new_title}'"
                    )
                else:
                    old_title = activity.title
                    activity.title = new_title
                    logger.info(f"Updated {activity.id}: '{old_title}' -> '{new_title}'")

                stats["titles_updated"] += 1

                # Log progress every 500 activities
                if (i + 1) % 500 == 0:
                    logger.info(f"Progress: {i + 1}/{len(activities)} activities processed")

            except Exception as e:
                stats["errors"] += 1
                logger.error(f"Error processing activity {activity.id}: {e}")
                continue

        if not dry_run:
            db.commit()
            logger.info("Backfill complete - changes committed")
        else:
            logger.info("DRY RUN complete - no changes made")

        logger.info(
            f"Activity title backfill complete: "
            f"dry_run={dry_run}, "
            f"activities_found={stats['activities_found']}, "
            f"titles_updated={stats['titles_updated']}, "
            f"already_good={stats['already_good']}, "
            f"errors={stats['errors']}"
        )
    except Exception as e:
        db.rollback()
        logger.exception(f"Fatal error in backfill: {e}")
        raise
    else:
        return stats
    finally:
        db.close()


def main() -> int:
    """Main entry point for the backfill script."""
    parser = argparse.ArgumentParser(
        description="Backfill generic Strava activity titles",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the backfill (default: dry run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of activities to process (0 = no limit)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    try:
        stats = backfill_activity_titles(dry_run=dry_run, limit=args.limit)
        logger.info(f"Backfill completed successfully: {stats}")
    except Exception as e:
        logger.exception(f"Backfill failed: {e}")
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
