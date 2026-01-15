"""Backfill script for paired activities to create workouts, executions, and compliance.

This script processes activities that are paired with planned sessions but don't have:
- activity.workout_id set to the planned session's workout
- WorkoutExecution records
- Compliance metrics calculated

Usage:
    From project root:
    python scripts/backfill_paired_activities.py [--no-dry-run]

    Or as a module:
    python -m scripts.backfill_paired_activities [--no-dry-run]

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
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.db.session import SessionLocal
from app.workouts.compliance_service import ComplianceService
from app.workouts.execution_models import WorkoutComplianceSummary, WorkoutExecution
from app.workouts.models import Workout
from app.workouts.workout_factory import WorkoutFactory


def _process_activity_workout(
    db: Session,
    planned_session: PlannedSession,
    stats: dict[str, int],
) -> Workout | None:
    """Get or create workout for planned session.

    Args:
        db: Database session
        planned_session: Planned session for activity
        stats: Statistics dictionary to update

    Returns:
        Workout if successful, None otherwise
    """
    try:
        workout = WorkoutFactory.get_or_create_for_planned_session(db, planned_session)
        if planned_session.workout_id != workout.id:
            stats["workouts_created"] += 1
            logger.debug(f"Created workout {workout.id} for planned session {planned_session.id}")
            return workout
        logger.debug(f"Workout {workout.id} already exists for planned session {planned_session.id}")
        return workout
    except Exception as e:
        logger.error(
            f"Failed to get/create workout for planned session {planned_session.id}: {e}",
            exc_info=True,
        )
        stats["errors"] += 1
        return None
    else:
        return workout


def _update_activity_workout_id(
    activity: Activity,
    workout: Workout,
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Update activity.workout_id if needed.

    Args:
        activity: Activity to update
        workout: Workout to link
        stats: Statistics dictionary to update
        dry_run: Whether this is a dry run
    """
    if activity.workout_id != workout.id:
        if dry_run:
            logger.info(
                f"[DRY RUN] Would update activity {activity.id}.workout_id: "
                f"{activity.workout_id} -> {workout.id}",
            )
        else:
            activity.workout_id = workout.id
            logger.debug(f"Updated activity {activity.id}.workout_id to {workout.id}")
        stats["workout_ids_updated"] += 1
    else:
        logger.debug(f"Activity {activity.id} already has workout_id {workout.id}")


def _create_workout_execution(
    db: Session,
    workout: Workout,
    activity: Activity,
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Create WorkoutExecution if missing.

    Args:
        db: Database session
        workout: Workout to create execution for
        activity: Activity to link
        stats: Statistics dictionary to update
        dry_run: Whether this is a dry run
    """
    existing_execution = db.execute(
        select(WorkoutExecution).where(
            WorkoutExecution.workout_id == workout.id,
            WorkoutExecution.activity_id == activity.id,
        )
    ).scalar_one_or_none()

    if not existing_execution:
        if dry_run:
            logger.info(
                f"[DRY RUN] Would create WorkoutExecution for "
                f"workout {workout.id}, activity {activity.id}",
            )
            stats["executions_created"] += 1
        else:
            try:
                WorkoutFactory.attach_activity(db, workout, activity)
                stats["executions_created"] += 1
                logger.debug(f"Created WorkoutExecution for workout {workout.id}, activity {activity.id}")
            except Exception as e:
                logger.warning(
                    f"Failed to create execution for workout {workout.id}, "
                    f"activity {activity.id}: {e}",
                    exc_info=True,
                )
                stats["errors"] += 1
    else:
        logger.debug(f"WorkoutExecution already exists for workout {workout.id}, activity {activity.id}")


def _generate_compliance(
    db: Session,
    workout: Workout,
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Generate compliance if missing.

    Args:
        db: Database session
        workout: Workout to generate compliance for
        stats: Statistics dictionary to update
        dry_run: Whether this is a dry run
    """
    existing_compliance = db.execute(
        select(WorkoutComplianceSummary).where(WorkoutComplianceSummary.workout_id == workout.id)
    ).scalar_one_or_none()

    if not existing_compliance:
        if dry_run:
            logger.info(f"[DRY RUN] Would generate compliance for workout {workout.id}")
            stats["compliance_generated"] += 1
        else:
            try:
                ComplianceService.compute_and_persist(db, workout.id)
                stats["compliance_generated"] += 1
                logger.debug(f"Generated compliance for workout {workout.id}")
            except Exception as e:
                logger.warning(
                    f"Failed to generate compliance for workout {workout.id}: {e}",
                    exc_info=True,
                )
                # Don't count as error - compliance can fail if streams_data missing
    else:
        logger.debug(f"Compliance already exists for workout {workout.id}")


def _process_single_activity(
    db: Session,
    activity: Activity,
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Process a single paired activity.

    Args:
        db: Database session
        activity: Activity to process
        stats: Statistics dictionary to update
        dry_run: Whether this is a dry run
    """
    # Get planned session
    planned_session = db.get(PlannedSession, activity.planned_session_id)
    if not planned_session:
        logger.warning(
            f"Activity {activity.id} has planned_session_id {activity.planned_session_id} "
            "but planned session not found - skipping"
        )
        stats["skipped"] += 1
        return

    # Step 2: Get or create workout for planned session
    workout = _process_activity_workout(db, planned_session, stats)
    if not workout:
        return

    # Step 3: Update activity.workout_id if needed
    _update_activity_workout_id(activity, workout, stats, dry_run)

    # Step 4: Create WorkoutExecution if missing
    _create_workout_execution(db, workout, activity, stats, dry_run)

    # Step 5: Generate compliance if missing
    _generate_compliance(db, workout, stats, dry_run)


def backfill_paired_activities(dry_run: bool = True) -> dict[str, int]:
    """Backfill workouts, executions, and compliance for paired activities.

    Steps:
    1. Find activities with planned_session_id set
    2. For each paired activity:
       a. Get/create workout for planned session
       b. Set activity.workout_id to planned workout
       c. Create WorkoutExecution if missing
       d. Generate compliance if missing

    Args:
        dry_run: If True, only log what would be done (default: True)

    Returns:
        Dictionary with counts of items processed
    """
    stats: dict[str, int] = {
        "paired_activities_found": 0,
        "workouts_created": 0,
        "workout_ids_updated": 0,
        "executions_created": 0,
        "compliance_generated": 0,
        "skipped": 0,
        "errors": 0,
    }

    db = SessionLocal()
    try:
        logger.info(f"Starting paired activities backfill (dry_run={dry_run})")

        # Step 1: Find all paired activities
        logger.info("Step 1: Finding paired activities...")
        paired_activities = db.execute(
            select(Activity).where(Activity.planned_session_id.isnot(None))
        ).scalars().all()

        logger.info(f"Found {len(paired_activities)} paired activities")
        stats["paired_activities_found"] = len(paired_activities)

        for activity in paired_activities:
            try:
                _process_single_activity(db, activity, stats, dry_run)
            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    f"Error processing paired activity {activity.id}: {e}",
                    exc_info=True,
                )
                db.rollback()
                continue

        if not dry_run:
            db.commit()
            logger.info("Backfill complete - changes committed")
        else:
            logger.info("DRY RUN complete - no changes made")

        logger.info(
            "Paired activities backfill complete",
            dry_run=dry_run,
            **stats,
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
        description="Backfill workouts, executions, and compliance for paired activities",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the backfill (default: dry run)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    try:
        stats = backfill_paired_activities(dry_run=dry_run)
        logger.info("Backfill completed successfully", **stats)
    except Exception as e:
        logger.exception(f"Backfill failed: {e}")
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
