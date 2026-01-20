"""Backfill script for coach feedback and activity titles.

This script processes activities that:
1. Have a paired workout with compliance data but no LLM interpretation
2. Are missing a meaningful title (uses Strava title or generates from workout type)

Usage:
    From project root:
    python scripts/backfill_coach_feedback_and_title.py [--no-dry-run] [--limit N]

    Or as a module:
    python -m scripts.backfill_coach_feedback_and_title [--no-dry-run] [--limit N]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
"""

from __future__ import annotations

import argparse
import asyncio
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

from app.db.models import Activity
from app.db.session import SessionLocal
from app.workouts.execution_models import WorkoutComplianceSummary, WorkoutExecution
from app.workouts.interpretation_service import InterpretationService
from app.workouts.models import Workout


def _is_generic_title(title: str | None) -> bool:
    """Check if title is a generic Strava-style auto-generated title.

    Strava auto-generates titles like:
    - Morning Run, Lunch Run, Afternoon Run, Evening Run, Night Run
    - Morning Ride, Lunch Ride, Afternoon Ride, Evening Ride, Night Ride
    - Morning Swim, Lunch Swim, Afternoon Swim, Evening Swim, Night Swim
    - etc.

    Args:
        title: Title to check

    Returns:
        True if title is generic/auto-generated
    """
    if not title:
        return True

    title_lower = title.lower().strip()

    # Time-of-day prefixes used by Strava
    time_prefixes = ["morning", "lunch", "afternoon", "evening", "night"]

    # Activity types used by Strava
    activity_types = [
        "run", "ride", "swim", "walk", "hike", "workout",
        "weight training", "yoga", "crossfit", "elliptical",
        "stair stepper", "rowing", "ski", "snowboard",
        "ice skate", "kayak", "surf", "windsurf", "kitesurf",
    ]

    # Check for "Time Activity" pattern (e.g., "Morning Run", "Lunch Swim")
    for prefix in time_prefixes:
        for activity in activity_types:
            if title_lower == f"{prefix} {activity}":
                return True

    # Also catch simple generic titles
    generic_exact = {
        "run", "running", "ride", "cycling", "swim", "swimming",
        "activity", "workout", "exercise", "training",
    }
    if title_lower in generic_exact:
        return True

    return False


def _get_workout_type(workout: Workout) -> str:
    """Extract workout type from workout structure or tags.

    Args:
        workout: The workout object

    Returns:
        Workout type string
    """
    # Try to get type from tags
    tags = workout.tags or {}
    if isinstance(tags, dict):
        workout_type = tags.get("type") or tags.get("intent") or tags.get("workout_type")
        if workout_type:
            return workout_type

    # Try to get from structure
    structure = workout.structure or {}
    if isinstance(structure, dict):
        workout_type = structure.get("type") or structure.get("intent")
        if workout_type:
            return workout_type

    # Fall back to name-based inference
    return workout.name or ""


def _get_title_from_workout(workout: Workout) -> str:
    """Generate a display title from workout.

    Uses workout name if meaningful, otherwise generates from type.

    Args:
        workout: The workout object

    Returns:
        Human-readable title
    """
    # First try the workout's own name if it's not generic
    if workout.name and not _is_generic_title(workout.name):
        return workout.name

    # Fall back to generating from workout type
    workout_type = _get_workout_type(workout)
    type_lower = workout_type.lower()

    title_map = {
        "threshold": "Threshold Run",
        "tempo": "Tempo Run",
        "interval": "Interval Session",
        "vo2": "VO2max Intervals",
        "recovery": "Recovery Run",
        "easy": "Easy Run",
        "long": "Long Run",
        "endurance": "Endurance Run",
        "aerobic": "Aerobic Run",
        "fartlek": "Fartlek Session",
        "race": "Race",
        "hill": "Hill Workout",
        "speed": "Speed Session",
        "progression": "Progression Run",
    }

    for key, title in title_map.items():
        if key in type_lower:
            return title

    return "Training Run"


def _needs_title_update(activity: Activity, workout: Workout) -> bool:
    """Check if activity needs a title update.

    Args:
        activity: Activity to check
        workout: Associated workout

    Returns:
        True if title should be updated
    """
    return _is_generic_title(activity.title)


def _needs_interpretation(compliance: WorkoutComplianceSummary) -> bool:
    """Check if workout compliance needs LLM interpretation.

    Args:
        compliance: Compliance summary to check

    Returns:
        True if interpretation is missing
    """
    return not compliance.llm_summary or not compliance.llm_verdict


async def _process_interpretation(
    db: Session,
    workout: Workout,
    compliance: WorkoutComplianceSummary,
    stats: dict[str, int],
    dry_run: bool,
    workout_id_str: str,
) -> None:
    """Generate LLM interpretation for a workout.

    Args:
        db: Database session
        workout: Workout to interpret
        compliance: Compliance summary
        stats: Statistics dictionary to update
        dry_run: Whether this is a dry run
        workout_id_str: Workout ID as string for database queries
    """
    if not _needs_interpretation(compliance):
        logger.debug(f"Workout {workout_id_str} already has interpretation")
        return

    if dry_run:
        logger.info(f"[DRY RUN] Would generate interpretation for workout {workout_id_str}")
        stats["interpretations_generated"] += 1
        return

    try:
        service = InterpretationService()
        success = await service.interpret_workout(db, workout_id_str)
        if success:
            stats["interpretations_generated"] += 1
            logger.info(f"Generated interpretation for workout {workout_id_str}")
        else:
            logger.warning(f"Interpretation generation returned False for workout {workout_id_str}")
    except ValueError as e:
        logger.warning(f"Cannot interpret workout {workout_id_str}: {e}")
    except Exception as e:
        logger.error(f"Failed to interpret workout {workout_id_str}: {e}", exc_info=True)
        stats["errors"] += 1


def _process_title(
    activity: Activity,
    workout: Workout,
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Update activity title if needed.

    Args:
        activity: Activity to update
        workout: Associated workout
        stats: Statistics dictionary to update
        dry_run: Whether this is a dry run
    """
    if not _needs_title_update(activity, workout):
        logger.debug(f"Activity {activity.id} already has good title: {activity.title}")
        return

    new_title = _get_title_from_workout(workout)

    # Don't update if the new title would be the same as old
    if new_title.lower() == (activity.title or "").lower():
        logger.debug(f"Activity {activity.id} title unchanged: {activity.title}")
        return

    if dry_run:
        logger.info(
            f"[DRY RUN] Would update activity {activity.id} title: "
            f"'{activity.title}' -> '{new_title}'"
        )
        stats["titles_updated"] += 1
        return

    activity.title = new_title
    stats["titles_updated"] += 1
    logger.info(f"Updated activity {activity.id} title: '{activity.title}' -> '{new_title}'")


async def _process_single_activity_with_execution(
    db: Session,
    activity: Activity,
    stats: dict[str, int],
    dry_run: bool,
) -> None:
    """Process a single activity that has a workout execution.

    Args:
        db: Database session
        activity: Activity to process (already confirmed to have execution)
        stats: Statistics dictionary to update
        dry_run: Whether this is a dry run
    """
    # Get workout execution for this activity
    execution = db.execute(
        select(WorkoutExecution).where(WorkoutExecution.activity_id == str(activity.id))
    ).scalar_one_or_none()

    if not execution:
        # This shouldn't happen since we joined on WorkoutExecution
        logger.warning(f"Activity {activity.id} unexpectedly has no workout execution")
        return

    # Get workout - ensure we use string ID
    workout_id_str = str(execution.workout_id)
    workout = db.get(Workout, workout_id_str)
    if not workout:
        logger.warning(f"Workout {workout_id_str} not found for activity {activity.id}")
        stats["skipped_no_workout"] += 1
        return

    # Get compliance summary - use string comparison
    compliance = db.execute(
        select(WorkoutComplianceSummary).where(
            WorkoutComplianceSummary.workout_id == workout_id_str
        )
    ).scalar_one_or_none()

    # Process title update
    _process_title(activity, workout, stats, dry_run)

    # Process LLM interpretation if compliance exists
    if compliance:
        await _process_interpretation(db, workout, compliance, stats, dry_run, workout_id_str)
    else:
        logger.debug(f"No compliance for workout {workout_id_str} - skipping interpretation")
        stats["skipped_no_compliance"] += 1


async def backfill_coach_feedback_and_title(
    dry_run: bool = True,
    limit: int = 0,
) -> dict[str, int]:
    """Backfill coach feedback and titles for activities.

    Steps:
    1. Find activities WITH workout executions (optimized query)
    2. For each activity:
       a. Update title if missing/generic
       b. Generate LLM interpretation if compliance exists but no interpretation

    Args:
        dry_run: If True, only log what would be done (default: True)
        limit: Maximum number of activities to process (0 = no limit)

    Returns:
        Dictionary with counts of items processed
    """
    stats: dict[str, int] = {
        "activities_found": 0,
        "titles_updated": 0,
        "interpretations_generated": 0,
        "skipped_no_workout": 0,
        "skipped_no_compliance": 0,
        "errors": 0,
    }

    db = SessionLocal()
    try:
        logger.info(f"Starting coach feedback and title backfill (dry_run={dry_run}, limit={limit})")

        # Find only activities that HAVE workout executions (much more efficient)
        query = (
            select(Activity)
            .join(WorkoutExecution, WorkoutExecution.activity_id == Activity.id)
            .order_by(Activity.starts_at.desc())
        )
        if limit > 0:
            query = query.limit(limit)

        activities = db.execute(query).scalars().all()

        logger.info(f"Found {len(activities)} activities with workout executions to process")
        stats["activities_found"] = len(activities)

        for i, activity in enumerate(activities):
            try:
                logger.info(f"Processing activity {i + 1}/{len(activities)}: {activity.id}")
                await _process_single_activity_with_execution(db, activity, stats, dry_run)
            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    f"Error processing activity {activity.id}: {e}",
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
            f"Coach feedback and title backfill complete: "
            f"dry_run={dry_run}, "
            f"activities_found={stats['activities_found']}, "
            f"titles_updated={stats['titles_updated']}, "
            f"interpretations_generated={stats['interpretations_generated']}, "
            f"skipped_no_workout={stats['skipped_no_workout']}, "
            f"skipped_no_compliance={stats['skipped_no_compliance']}, "
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
        description="Backfill coach feedback and titles for activities",
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
        stats = asyncio.run(backfill_coach_feedback_and_title(dry_run=dry_run, limit=args.limit))
        logger.info(f"Backfill completed successfully: {stats}")
    except Exception as e:
        logger.exception(f"Backfill failed: {e}")
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
