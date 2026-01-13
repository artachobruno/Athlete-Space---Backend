"""Backfill script to create workouts for existing planned sessions and activities.

PHASE 6: ONE-TIME script to backfill existing data.
Enforces the mandatory workout invariant:
- If training exists → a workout exists
- If activity exists → an execution exists

Usage:
    From project root:
    python scripts/backfill_workouts.py [--no-dry-run]

    Or as a module:
    python -m scripts.backfill_workouts [--no-dry-run]

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
from sqlalchemy import inspect, select, text

from app.db.models import Activity, PlannedSession
from app.db.session import SessionLocal
from app.workouts.compliance_service import ComplianceService
from app.workouts.execution_models import WorkoutComplianceSummary, WorkoutExecution
from app.workouts.models import Workout
from app.workouts.workout_factory import WorkoutFactory


def _check_schema(db) -> tuple[bool, bool]:
    """Check if required columns exist in the database.

    Returns:
        Tuple of (planned_sessions_has_workout_id, activities_has_workout_id)
    """
    try:
        inspector = inspect(db.bind) if hasattr(db, 'bind') and db.bind else None
        if not inspector:
            logger.warning("Could not inspect database schema - assuming columns exist")
            return True, True

        planned_sessions_columns = [col['name'] for col in inspector.get_columns('planned_sessions')]
        activities_columns = [col['name'] for col in inspector.get_columns('activities')]

        planned_has_workout_id = 'workout_id' in planned_sessions_columns
        activities_has_workout_id = 'workout_id' in activities_columns

        logger.info(
            "Schema check complete",
            planned_sessions_has_workout_id=planned_has_workout_id,
            activities_has_workout_id=activities_has_workout_id,
        )

        return planned_has_workout_id, activities_has_workout_id
    except Exception as e:
        logger.warning(f"Could not check schema: {e} - assuming columns exist")
        return True, True


def backfill_workouts(dry_run: bool = True) -> dict[str, int]:
    """Backfill workouts for existing planned sessions and activities.

    Steps:
    1. Planned sessions without workout → create
    2. Activities without workout → create inferred
    3. Activities without execution → create
    4. Generate compliance for all executions

    Args:
        dry_run: If True, only log what would be done (default: True)

    Returns:
        Dictionary with counts of items processed
    """
    stats: dict[str, int] = {
        "planned_sessions_processed": 0,
        "planned_sessions_created": 0,
        "activities_processed": 0,
        "activities_workouts_created": 0,
        "executions_created": 0,
        "compliance_generated": 0,
        "errors": 0,
    }

    db = SessionLocal()
    try:
        logger.info(f"Starting workout backfill (dry_run={dry_run})")

        # Check schema first
        planned_has_workout_id, activities_has_workout_id = _check_schema(db)
        
        if not planned_has_workout_id or not activities_has_workout_id:
            logger.error(
                "Required columns missing in database schema!",
                planned_sessions_has_workout_id=planned_has_workout_id,
                activities_has_workout_id=activities_has_workout_id,
            )
            logger.error(
                "Please run migrations first: python scripts/run_migrations.py"
            )
            raise ValueError(
                f"Missing workout_id columns. "
                f"planned_sessions.workout_id exists: {planned_has_workout_id}, "
                f"activities.workout_id exists: {activities_has_workout_id}. "
                f"Run migrations first."
            )

        # Step 1: Planned sessions without workout
        logger.info("Step 1: Processing planned sessions without workout...")
        
        # First, check if workout_id column exists
        try:
            result = db.execute(
                select(PlannedSession).where(PlannedSession.workout_id.is_(None))
            ).scalars().all()
            planned_sessions_without_workout = list(result)
        except Exception as column_error:
            logger.error(
                f"Error querying planned_sessions.workout_id - column may not exist: {column_error}",
                error_type=type(column_error).__name__,
                exc_info=True,
            )
            # Try to check if column exists
            from sqlalchemy import inspect, text
            inspector = inspect(db.bind) if hasattr(db, 'bind') else None
            if inspector:
                try:
                    columns = [col['name'] for col in inspector.get_columns('planned_sessions')]
                    logger.info(f"Columns in planned_sessions table: {columns}")
                    has_workout_id = 'workout_id' in columns
                    logger.info(f"workout_id column exists: {has_workout_id}")
                except Exception as inspect_error:
                    logger.warning(f"Could not inspect table schema: {inspect_error}")
            
            # If workout_id doesn't exist, skip this step
            logger.warning("Skipping planned sessions step - workout_id column may not exist. Run migrations first.")
            planned_sessions_without_workout = []

        logger.info(f"Found {len(planned_sessions_without_workout)} planned sessions without workout")

        for planned_session in planned_sessions_without_workout:
            stats["planned_sessions_processed"] += 1
            try:
                # Debug: Log what type of object we got
                logger.debug(
                    f"Processing planned_session",
                    object_type=type(planned_session).__name__,
                    is_dict=isinstance(planned_session, dict),
                    has_id_attr=hasattr(planned_session, "id"),
                    dir_sample=list(dir(planned_session))[:10] if hasattr(planned_session, "__dict__") else None,
                )
                
                # Ensure planned_session has an id
                session_id = getattr(planned_session, "id", None)
                if session_id is None:
                    logger.error(
                        "Planned session missing id attribute - skipping",
                        planned_session_type=type(planned_session).__name__,
                        planned_session_repr=repr(planned_session)[:200],
                        is_dict=isinstance(planned_session, dict),
                        keys=list(planned_session.keys()) if isinstance(planned_session, dict) else None,
                    )
                    stats["errors"] += 1
                    continue

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would create workout for planned session: "
                        f"id={session_id}, title={getattr(planned_session, 'title', 'N/A')}, "
                        f"date={getattr(planned_session, 'date', 'N/A')}"
                    )
                    stats["planned_sessions_created"] += 1
                else:
                    WorkoutFactory.get_or_create_for_planned_session(db, planned_session)
                    stats["planned_sessions_created"] += 1
                    logger.debug(
                        f"Created workout for planned session: id={session_id}"
                    )
            except KeyError as e:
                stats["errors"] += 1
                # Don't access planned_session.id here - session may be rolled back
                try:
                    session_id = str(getattr(planned_session, "id", "unknown"))
                except Exception:
                    session_id = "unknown (session rolled back)"
                error_msg = str(e).replace("{", "{{").replace("}", "}}")  # Escape for loguru
                logger.error(
                    "KeyError creating workout for planned session",
                    session_id=session_id,
                    error_type=type(e).__name__,
                    error_message=error_msg,
                    exc_info=True,
                )
                db.rollback()  # Ensure session is rolled back
            except Exception as e:
                stats["errors"] += 1
                # Don't access planned_session.id here - session may be rolled back
                try:
                    session_id = str(getattr(planned_session, "id", "unknown"))
                except Exception:
                    session_id = "unknown (session rolled back)"
                error_msg = str(e).replace("{", "{{").replace("}", "}}")  # Escape for loguru
                logger.error(
                    "Error creating workout for planned session",
                    session_id=session_id,
                    error_type=type(e).__name__,
                    error_message=error_msg,
                    exc_info=True,
                )
                db.rollback()  # Ensure session is rolled back

        if not dry_run:
            db.commit()
            logger.info(f"Step 1 complete: Created {stats['planned_sessions_created']} workouts for planned sessions")

        # Step 2: Activities without workout
        logger.info("Step 2: Processing activities without workout...")
        activities_without_workout = db.execute(
            select(Activity).where(Activity.workout_id.is_(None))
        ).scalars().all()

        logger.info(f"Found {len(activities_without_workout)} activities without workout")

        for activity in activities_without_workout:
            stats["activities_processed"] += 1
            try:
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would create workout for activity: "
                        f"id={activity.id}, type={activity.type}, start_time={activity.start_time}"
                    )
                    stats["activities_workouts_created"] += 1
                else:
                    WorkoutFactory.get_or_create_for_activity(db, activity)
                    stats["activities_workouts_created"] += 1
                    logger.debug(
                        f"Created workout for activity: id={activity.id}"
                    )
            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    f"Error creating workout for activity {activity.id}: {e}",
                    exc_info=True,
                )

        if not dry_run:
            db.commit()
            logger.info(f"Step 2 complete: Created {stats['activities_workouts_created']} workouts for activities")

        # Step 3: Activities without execution
        logger.info("Step 3: Processing activities without execution...")
        all_activities = db.execute(select(Activity)).scalars().all()
        activities_without_execution = []

        for activity in all_activities:
            if not activity.workout_id:
                continue

            # Check if execution exists
            execution = db.execute(
                select(WorkoutExecution).where(WorkoutExecution.activity_id == activity.id)
            ).scalar_one_or_none()

            if not execution:
                activities_without_execution.append(activity)

        logger.info(f"Found {len(activities_without_execution)} activities without execution")

        for activity in activities_without_execution:
            try:
                workout = db.execute(
                    select(Workout).where(Workout.id == activity.workout_id)
                ).scalar_one_or_none()

                if not workout:
                    logger.warning(f"Activity {activity.id} has workout_id {activity.workout_id} but workout not found")
                    stats["errors"] += 1
                    continue

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would create execution for activity: "
                        f"id={activity.id}, workout_id={workout.id}"
                    )
                    stats["executions_created"] += 1
                else:
                    WorkoutFactory.attach_activity(db, workout, activity)
                    stats["executions_created"] += 1
                    logger.debug(
                        f"Created execution for activity: id={activity.id}, workout_id={workout.id}"
                    )
            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    f"Error creating execution for activity {activity.id}: {e}",
                    exc_info=True,
                )

        if not dry_run:
            db.commit()
            logger.info(f"Step 3 complete: Created {stats['executions_created']} executions")

        # Step 4: Generate compliance for all executions
        logger.info("Step 4: Generating compliance for executions...")
        all_executions = db.execute(select(WorkoutExecution)).scalars().all()

        executions_without_compliance = []
        for execution in all_executions:
            compliance = db.execute(
                select(WorkoutComplianceSummary).where(
                    WorkoutComplianceSummary.workout_id == execution.workout_id
                )
            ).scalar_one_or_none()

            if not compliance:
                executions_without_compliance.append(execution)

        logger.info(f"Found {len(executions_without_compliance)} executions without compliance")

        for execution in executions_without_compliance:
            try:
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would generate compliance for execution: "
                        f"id={execution.id}, workout_id={execution.workout_id}"
                    )
                    stats["compliance_generated"] += 1
                else:
                    ComplianceService.compute_and_persist(db, execution.workout_id)
                    stats["compliance_generated"] += 1
                    logger.debug(
                        f"Generated compliance for execution: id={execution.id}, workout_id={execution.workout_id}"
                    )
            except Exception as e:
                stats["errors"] += 1
                logger.warning(
                    f"Error generating compliance for execution {execution.id}: {e}",
                    exc_info=True,
                )
                # Don't fail on compliance errors - it's non-critical

        if not dry_run:
            db.commit()
            logger.info(f"Step 4 complete: Generated {stats['compliance_generated']} compliance summaries")

        logger.info(
            "Backfill complete",
            dry_run=dry_run,
            **stats,
        )

    except KeyError as e:
        logger.error(
            f"Fatal KeyError during backfill: {e}",
            error_key=str(e),
            error_type=type(e).__name__,
            exc_info=True,
        )
        db.rollback()
        raise
    except Exception as e:
        logger.error(
            f"Fatal error during backfill: {e}",
            error_type=type(e).__name__,
            error_msg=str(e),
            exc_info=True,
        )
        db.rollback()
        raise
    finally:
        db.close()

    return stats


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Backfill workouts for existing data")
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually execute the backfill (default: dry-run mode)",
    )
    args = parser.parse_args()

    dry_run = not args.no_dry_run

    if dry_run:
        logger.warning("⚠️  Running in DRY-RUN mode. Use --no-dry-run to actually execute.")
    else:
        logger.warning("⚠️  EXECUTING BACKFILL - This will modify the database!")

    try:
        stats = backfill_workouts(dry_run=dry_run)

        logger.info("=" * 80)
        logger.info("BACKFILL SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Planned sessions processed: {stats['planned_sessions_processed']}")
        logger.info(f"Planned session workouts created: {stats['planned_sessions_created']}")
        logger.info(f"Activities processed: {stats['activities_processed']}")
        logger.info(f"Activity workouts created: {stats['activities_workouts_created']}")
        logger.info(f"Executions created: {stats['executions_created']}")
        logger.info(f"Compliance summaries generated: {stats['compliance_generated']}")
        logger.info(f"Errors: {stats['errors']}")

        if dry_run:
            logger.info("=" * 80)
            logger.info("This was a DRY RUN. Use --no-dry-run to actually execute.")
            logger.info("=" * 80)

        return 0 if stats["errors"] == 0 else 1

    except KeyError as e:
        import traceback
        error_msg = str(e).replace("{", "{{").replace("}", "}}")
        logger.error(
            "Backfill failed with KeyError: {}",
            error_msg,
            error_key=str(e),
            error_repr=repr(e),
            traceback=traceback.format_exc(),
        )
        traceback.print_exc()
        return 1
    except Exception as e:
        import traceback
        error_msg = str(e).replace("{", "{{").replace("}", "}}")
        logger.error(
            "Backfill failed: {}",
            error_msg,
            error_type=type(e).__name__,
            error_repr=repr(e),
            traceback=traceback.format_exc(),
        )
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
