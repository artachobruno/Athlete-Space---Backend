"""Script to fix user_id mismatches between activities and planned sessions.

This script identifies and fixes cases where activities and planned sessions
have different user_ids but the same athlete_id. It maps athlete_id to the
correct user_id via StravaAccount and updates incorrect records.

Usage:
    From project root:
    python scripts/fix_user_id_mismatches.py [--no-dry-run] [--athlete-id ATHLETE_ID]

    Or as a module:
    python -m scripts.fix_user_id_mismatches [--no-dry-run] [--athlete-id ATHLETE_ID]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
    - Can filter by specific athlete_id for testing
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
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession, StravaAccount
from app.db.session import SessionLocal
from app.workouts.models import Workout


def get_correct_user_id_for_athlete(session: Session, athlete_id: int) -> str | None:
    """Get correct user_id for athlete_id via StravaAccount.

    Args:
        session: Database session
        athlete_id: Strava athlete ID

    Returns:
        User ID string or None if not found
    """
    account = session.execute(
        select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id))
    ).first()

    if account:
        return account[0].user_id
    return None


def find_and_fix_mismatches(
    db: Session,
    athlete_id: int | None = None,
    dry_run: bool = True,
) -> dict[str, int]:
    """Find and fix user_id mismatches.

    Args:
        db: Database session
        athlete_id: Optional athlete_id to filter by (for testing)
        dry_run: If True, only log what would be done without making changes

    Returns:
        Dictionary with statistics about the processing
    """
    # Ensure Workout model is loaded into SQLAlchemy metadata for foreign key resolution
    _ = Workout.__table__  # Force metadata loading

    stats: dict[str, int] = {
        "activities_checked": 0,
        "activities_fixed": 0,
        "planned_sessions_checked": 0,
        "planned_sessions_fixed": 0,
        "errors": 0,
    }

    # Get all StravaAccount mappings (athlete_id -> user_id)
    accounts_query = select(StravaAccount)
    accounts = list(db.scalars(accounts_query).all())

    # Build athlete_id -> correct_user_id mapping
    athlete_to_user: dict[int, str] = {}
    for account in accounts:
        try:
            athlete_id_int = int(account.athlete_id)
            athlete_to_user[athlete_id_int] = account.user_id
        except (ValueError, TypeError):
            logger.warning(f"Invalid athlete_id in StravaAccount: {account.athlete_id}")
            continue

    logger.info(f"Found {len(athlete_to_user)} athlete_id -> user_id mappings")

    if athlete_id:
        logger.info(f"Filtering by athlete_id: {athlete_id}")
        athlete_to_user = {aid: uid for aid, uid in athlete_to_user.items() if aid == athlete_id}

    # Process Activities
    logger.info("=" * 80)
    logger.info("Checking Activities...")
    logger.info("=" * 80)

    activities_query = select(Activity).where(Activity.athlete_id.isnot(None))
    if athlete_id:
        activities_query = activities_query.where(Activity.athlete_id == str(athlete_id))

    activities = list(db.scalars(activities_query).all())
    stats["activities_checked"] = len(activities)

    for activity in activities:
        try:
            activity_athlete_id = int(activity.athlete_id) if activity.athlete_id else None
            if not activity_athlete_id:
                continue

            correct_user_id = athlete_to_user.get(activity_athlete_id)
            if not correct_user_id:
                logger.debug(
                    f"Activity {activity.id}: No StravaAccount found for athlete_id={activity_athlete_id}, skipping"
                )
                continue

            if activity.user_id == correct_user_id:
                continue  # Already correct

            logger.info(
                f"Activity {activity.id}: athlete_id={activity_athlete_id}, "
                f"current user_id={activity.user_id}, correct user_id={correct_user_id}"
            )

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would update activity {activity.id} user_id from "
                    f"{activity.user_id} to {correct_user_id}"
                )
            else:
                activity.user_id = correct_user_id
                stats["activities_fixed"] += 1
                logger.info(
                    f"✅ Updated activity {activity.id} user_id to {correct_user_id}"
                )

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                f"Error processing activity {activity.id}: {e}",
                exc_info=True,
            )

    # Process PlannedSessions
    logger.info("=" * 80)
    logger.info("Checking Planned Sessions...")
    logger.info("=" * 80)

    planned_query = select(PlannedSession).where(PlannedSession.athlete_id.isnot(None))
    if athlete_id:
        planned_query = planned_query.where(PlannedSession.athlete_id == athlete_id)

    planned_sessions = list(db.scalars(planned_query).all())
    stats["planned_sessions_checked"] = len(planned_sessions)

    for planned in planned_sessions:
        try:
            correct_user_id = athlete_to_user.get(planned.athlete_id)
            if not correct_user_id:
                logger.debug(
                    f"Planned session {planned.id}: No StravaAccount found for "
                    f"athlete_id={planned.athlete_id}, skipping"
                )
                continue

            if planned.user_id == correct_user_id:
                continue  # Already correct

            logger.info(
                f"Planned session {planned.id}: athlete_id={planned.athlete_id}, "
                f"current user_id={planned.user_id}, correct user_id={correct_user_id}"
            )

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would update planned session {planned.id} user_id from "
                    f"{planned.user_id} to {correct_user_id}"
                )
            else:
                planned.user_id = correct_user_id
                stats["planned_sessions_fixed"] += 1
                logger.info(
                    f"✅ Updated planned session {planned.id} user_id to {correct_user_id}"
                )

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                f"Error processing planned session {planned.id}: {e}",
                exc_info=True,
            )

    return stats


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Fix user_id mismatches between activities and planned sessions"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the fixes (default: dry-run mode)",
    )
    parser.add_argument(
        "--athlete-id",
        type=int,
        default=None,
        help="Filter by specific athlete_id (for testing)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    logger.info("=" * 80)
    logger.info("Fix User ID Mismatches Script")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    if args.athlete_id:
        logger.info(f"Filter: athlete_id={args.athlete_id}")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        stats = find_and_fix_mismatches(
            db=db,
            athlete_id=args.athlete_id,
            dry_run=dry_run,
        )

        if not dry_run:
            db.commit()

        logger.info("=" * 80)
        logger.info("Summary:")
        logger.info(f"  Activities checked: {stats['activities_checked']}")
        logger.info(f"  Planned sessions checked: {stats['planned_sessions_checked']}")
        if dry_run:
            logger.info("  (Run with --no-dry-run to actually fix)")
        else:
            logger.info(f"  Activities fixed: {stats['activities_fixed']}")
            logger.info(f"  Planned sessions fixed: {stats['planned_sessions_fixed']}")
            logger.info(f"  Errors: {stats['errors']}")
        logger.info("=" * 80)

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
