"""Backfill script to populate target pace/HR/power for existing workout steps.

This script calculates and populates target values for workout steps based on:
- Step intensity_zone (if available)
- User settings (threshold_pace_ms, threshold_hr, ftp_watts)

Usage:
    From project root:
    python scripts/backfill_workout_step_targets.py [--no-dry-run]

    Or as a module:
    python -m scripts.backfill_workout_step_targets [--no-dry-run]

Safety:
    - DRY_RUN = True by default
    - Logs everything before making changes
    - Use --no-dry-run to actually execute
"""

from __future__ import annotations

import argparse
import sys
import traceback
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

from app.db.models import UserSettings
from app.db.session import SessionLocal
from app.workouts.canonical import StepIntensity, StepTargetType
from app.workouts.models import Workout, WorkoutStep
from app.workouts.target_calculation import calculate_target_from_intensity


def _map_intensity_zone_to_intensity(intensity_zone: str | None) -> StepIntensity | None:
    """Map intensity_zone string to StepIntensity enum.

    Args:
        intensity_zone: Intensity zone string (e.g., "easy", "tempo", "threshold")

    Returns:
        StepIntensity enum or None if cannot map
    """
    if not intensity_zone:
        return None

    intensity_lower = intensity_zone.lower().strip()
    intensity_map: dict[str, StepIntensity] = {
        "easy": StepIntensity.EASY,
        "tempo": StepIntensity.TEMPO,
        "lt2": StepIntensity.LT2,
        "threshold": StepIntensity.THRESHOLD,
        "vo2": StepIntensity.VO2,
        "vo2max": StepIntensity.VO2,
        "flow": StepIntensity.FLOW,
        "rest": StepIntensity.REST,
        "recovery": StepIntensity.REST,
    }
    return intensity_map.get(intensity_lower)


def _map_step_type_to_intensity(step_type: str | None) -> StepIntensity | None:
    """Map step type to intensity (fallback when intensity_zone is missing).

    Args:
        step_type: Step type string (e.g., "steady", "interval", "recovery")

    Returns:
        StepIntensity enum or None if cannot map
    """
    if not step_type:
        return None

    step_type_lower = step_type.lower().strip()
    if step_type_lower in {"warmup", "cooldown", "recovery", "rest"}:
        return StepIntensity.EASY
    if step_type_lower == "steady":
        return StepIntensity.FLOW
    if step_type_lower == "interval":
        return StepIntensity.THRESHOLD

    return None


def backfill_step_targets(dry_run: bool = True) -> dict[str, int]:
    """Backfill target values for workout steps.

    Args:
        dry_run: If True, only log what would be done (don't commit changes)

    Returns:
        Dictionary with counts of updated steps
    """
    stats: dict[str, int] = {
        "total_steps": 0,
        "steps_without_targets": 0,
        "steps_with_targets": 0,
        "steps_updated": 0,
        "steps_skipped_no_intensity": 0,
        "steps_skipped_no_user_settings": 0,
        "errors": 0,
    }

    with SessionLocal() as session:
        # Get all workout steps
        steps_result = session.execute(
            select(WorkoutStep).join(Workout).order_by(WorkoutStep.workout_id, WorkoutStep.order)
        )
        all_steps = steps_result.scalars().all()

        stats["total_steps"] = len(all_steps)

        logger.info(f"Found {stats['total_steps']} workout steps to process")

        # Process each step
        for step in all_steps:
            try:
                # Check if step already has target
                if step.target_metric:
                    stats["steps_with_targets"] += 1
                    continue

                stats["steps_without_targets"] += 1

                # Get workout to access sport and user_id
                workout = session.execute(
                    select(Workout).where(Workout.id == step.workout_id)
                ).scalar_one_or_none()

                if not workout:
                    logger.warning(f"Workout {step.workout_id} not found for step {step.id}")
                    stats["errors"] += 1
                    continue

                # Get user settings
                user_settings_result = session.execute(
                    select(UserSettings).where(UserSettings.user_id == workout.user_id)
                ).first()
                user_settings = user_settings_result[0] if user_settings_result else None

                if not user_settings:
                    logger.debug(
                        f"No user settings found for user {workout.user_id}, workout {workout.id}, step {step.id}"
                    )
                    stats["steps_skipped_no_user_settings"] += 1
                    continue

                # Determine intensity from intensity_zone or step type
                intensity: StepIntensity | None = None

                # First try intensity_zone
                if step.intensity_zone:
                    intensity = _map_intensity_zone_to_intensity(step.intensity_zone)

                # Fallback to step type if intensity_zone doesn't map
                if not intensity:
                    intensity = _map_step_type_to_intensity(step.type)

                if not intensity:
                    logger.debug(
                        f"Cannot determine intensity for step {step.id} "
                        f"(type={step.type}, intensity_zone={step.intensity_zone})"
                    )
                    stats["steps_skipped_no_intensity"] += 1
                    continue

                # Calculate target from intensity
                calc_target_type, calc_min, calc_max, calc_value = calculate_target_from_intensity(
                    intensity=intensity,
                    sport=workout.sport,
                    user_settings=user_settings,
                )

                if calc_target_type == StepTargetType.NONE:
                    logger.debug(f"Cannot calculate target for step {step.id} (intensity={intensity.value})")
                    stats["steps_skipped_no_intensity"] += 1
                    continue

                # Update step
                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would update step {step.id} (workout {workout.id}, order {step.order}): "
                        f"target_metric={calc_target_type.value}, "
                        f"target_min={calc_min}, target_max={calc_max}, target_value={calc_value}"
                    )
                else:
                    step.target_metric = calc_target_type.value
                    step.target_min = calc_min
                    step.target_max = calc_max
                    step.target_value = calc_value
                    logger.debug(
                        f"Updated step {step.id} (workout {workout.id}, order {step.order}): "
                        f"target_metric={calc_target_type.value}"
                    )

                stats["steps_updated"] += 1

            except Exception as e:
                logger.exception(f"Error processing step {step.id}: {e}")
                stats["errors"] += 1

        if not dry_run:
            session.commit()
            logger.info("Changes committed to database")
        else:
            session.rollback()
            logger.info("[DRY RUN] No changes committed")

    return stats


def main() -> None:
    """Main entry point for the backfill script."""
    parser = argparse.ArgumentParser(description="Backfill target values for workout steps")
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="Actually commit changes (default is dry-run mode)",
    )

    args = parser.parse_args()
    dry_run = not args.no_dry_run

    logger.info(f"Starting backfill of workout step targets (dry_run={dry_run})")

    try:
        stats = backfill_step_targets(dry_run=dry_run)

        logger.info("=" * 60)
        logger.info("Backfill Summary:")
        logger.info(f"  Total steps: {stats['total_steps']}")
        logger.info(f"  Steps with targets (skipped): {stats['steps_with_targets']}")
        logger.info(f"  Steps without targets: {stats['steps_without_targets']}")
        logger.info(f"  Steps updated: {stats['steps_updated']}")
        logger.info(f"  Steps skipped (no intensity): {stats['steps_skipped_no_intensity']}")
        logger.info(f"  Steps skipped (no user settings): {stats['steps_skipped_no_user_settings']}")
        logger.info(f"  Errors: {stats['errors']}")
        logger.info("=" * 60)

        if dry_run:
            logger.info("\n[DRY RUN] No changes were made. Use --no-dry-run to commit changes.")

    except Exception as e:
        logger.exception(f"Backfill failed: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
