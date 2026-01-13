"""Backfill script to create workouts for existing PlannedSession-Activity matches.

This script finds all PlannedSessions that have completed_activity_id set
but don't have a corresponding Workout, and creates workouts for them.

Usage:
    From project root:
    python scripts/backfill_workouts_for_matches.py [--dry-run]

    Or as a module:
    python -m scripts.backfill_workouts_for_matches [--dry-run]
"""

from __future__ import annotations

import argparse
import sys

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlannedSession
from app.db.session import SessionLocal
from app.workouts.models import Workout
from app.workouts.workout_factory import ensure_workout_for_match


def backfill_workouts_for_matches(*, dry_run: bool = False) -> dict[str, int]:
    """Backfill workouts for existing PlannedSession-Activity matches.

    Finds all PlannedSessions with completed_activity_id that don't have
    a corresponding Workout, and creates workouts for them.

    Args:
        dry_run: If True, only report what would be done without making changes

    Returns:
        Dictionary with counts: {'processed', 'created', 'skipped', 'errors'}
    """
    logger.info(f"Starting backfill for workouts (dry_run={dry_run})")

    stats: dict[str, int] = {
        "processed": 0,
        "created": 0,
        "skipped": 0,
        "errors": 0,
    }

    db = SessionLocal()
    try:
        # Find all planned sessions with completed_activity_id
        planned_sessions = db.execute(
            select(PlannedSession).where(PlannedSession.completed_activity_id.isnot(None))
        ).scalars().all()

        logger.info(f"Found {len(planned_sessions)} planned sessions with completed_activity_id")

        for planned_session in planned_sessions:
            stats["processed"] += 1

            if not planned_session.completed_activity_id:
                stats["skipped"] += 1
                continue

            # Check if workout already exists
            existing_workout = db.execute(
                select(Workout).where(
                    Workout.activity_id == planned_session.completed_activity_id,
                    Workout.planned_session_id == planned_session.id,
                )
            ).scalar_one_or_none()

            if existing_workout:
                logger.debug(
                    "Workout already exists, skipping",
                    planned_session_id=planned_session.id,
                    activity_id=planned_session.completed_activity_id,
                    workout_id=existing_workout.id,
                )
                stats["skipped"] += 1
                continue

            if dry_run:
                logger.info(
                    "[DRY RUN] Would create workout",
                    planned_session_id=planned_session.id,
                    activity_id=planned_session.completed_activity_id,
                    user_id=planned_session.user_id,
                )
                stats["created"] += 1
                continue

            # Create workout
            try:
                workout = ensure_workout_for_match(
                    user_id=planned_session.user_id,
                    activity_id=planned_session.completed_activity_id,
                    planned_session_id=planned_session.id,
                    db=db,
                )
                db.commit()

                logger.info(
                    "Created workout for existing match",
                    workout_id=workout.id,
                    planned_session_id=planned_session.id,
                    activity_id=planned_session.completed_activity_id,
                )
                stats["created"] += 1

            except Exception as e:
                logger.error(
                    "Failed to create workout",
                    planned_session_id=planned_session.id,
                    activity_id=planned_session.completed_activity_id,
                    error=str(e),
                )
                db.rollback()
                stats["errors"] += 1
                continue

        logger.info(
            "Backfill completed",
            dry_run=dry_run,
            **stats,
        )

    except Exception as e:
        logger.error(f"Error during backfill: {e}")
        db.rollback()
        raise
    finally:
        db.close()

    return stats


def main() -> int:
    """Main entry point for the backfill script."""
    parser = argparse.ArgumentParser(description="Backfill workouts for existing matches")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (don't make changes)",
    )
    args = parser.parse_args()

    try:
        stats = backfill_workouts_for_matches(dry_run=args.dry_run)
        logger.info("Backfill script completed successfully", **stats)

        if args.dry_run:
            logger.info("DRY RUN MODE: No changes were made")
            logger.info(f"Would create {stats['created']} workouts")
            logger.info(f"Would skip {stats['skipped']} (already exist)")
        else:
            logger.info(f"Created {stats['created']} workouts")
            logger.info(f"Skipped {stats['skipped']} (already exist)")
            if stats["errors"] > 0:
                logger.warning(f"Encountered {stats['errors']} errors")

        return 0 if stats["errors"] == 0 else 1

    except Exception as e:
        logger.exception("Backfill script failed", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
