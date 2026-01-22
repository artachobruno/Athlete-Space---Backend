"""Conversion utilities between canonical and database models.

This module handles conversion between the canonical Pydantic schema
(used for LLM output and validation) and the database SQLAlchemy models.
"""

from __future__ import annotations

import uuid

from app.db.models import UserSettings
from app.workouts.canonical import StepIntensity, StepTargetType, StructuredWorkout, WorkoutStep
from app.workouts.models import Workout as DBWorkout
from app.workouts.models import WorkoutStep as DBWorkoutStep
from app.workouts.step_utils import infer_step_name
from app.workouts.target_calculation import calculate_target_from_intensity
from app.workouts.targets_schema import DurationDistance, DurationTime, StepTargets, TargetRange, TargetSingleValue
from app.workouts.targets_utils import legacy_to_targets


def intensity_to_step_type(intensity: StepIntensity) -> str:
    """Convert canonical intensity to database step type.

    Args:
        intensity: Canonical intensity enum

    Returns:
        Database step type string
    """
    mapping: dict[StepIntensity, str] = {
        StepIntensity.EASY: "steady",
        StepIntensity.TEMPO: "steady",
        StepIntensity.LT2: "interval",
        StepIntensity.THRESHOLD: "interval",
        StepIntensity.VO2: "interval",
        StepIntensity.FLOW: "steady",
        StepIntensity.REST: "recovery",
    }
    return mapping.get(intensity, "steady")


def canonical_step_to_db_step(
    canonical_step: WorkoutStep,
    workout_id: str,
    step_id: str,
    sport: str | None = None,
    user_settings: UserSettings | None = None,
) -> DBWorkoutStep:
    """Convert canonical workout step to database model.

    Args:
        canonical_step: Canonical workout step
        workout_id: Workout ID
        step_id: Step ID
        sport: Sport type (for target calculation)
        user_settings: User settings (for target calculation)

    Returns:
        Database WorkoutStep model
    """
    step_type = intensity_to_step_type(canonical_step.intensity)
    if canonical_step.is_recovery:
        step_type = "recovery"

    # Calculate target values from intensity and user thresholds
    target_metric = None
    target_min = None
    target_max = None
    target_value = None

    if sport and user_settings:
        calculated_target_type, calc_min, calc_max, calc_value = calculate_target_from_intensity(
            intensity=canonical_step.intensity,
            sport=sport,
            user_settings=user_settings,
        )
        if calculated_target_type != StepTargetType.NONE:
            target_metric = calculated_target_type.value
            # Convert pace from m/s to min/km if needed
            if calculated_target_type == StepTargetType.PACE:
                # Convert m/s to min/km: (1000 meters) / (velocity m/s * 60 seconds/min)
                if calc_min is not None:
                    target_min = 1000.0 / (calc_min * 60.0) if calc_min > 0 else None
                if calc_max is not None:
                    target_max = 1000.0 / (calc_max * 60.0) if calc_max > 0 else None
                if calc_value is not None:
                    target_value = 1000.0 / (calc_value * 60.0) if calc_value > 0 else None
            else:
                target_min = calc_min
                target_max = calc_max
                target_value = calc_value
    
    # Default to pace for running if no target was calculated and sport is running
    if not target_metric and sport and sport.lower() == "run":
        target_metric = StepTargetType.PACE.value
        # If we have user_settings but no threshold_pace_ms, leave min/max as None
        # The frontend can handle displaying pace targets even without specific values
    
    # Fallback to canonical target_type if no user settings and no default
    if not target_metric and canonical_step.target_type != StepTargetType.NONE:
        target_metric = canonical_step.target_type.value

    # Ensure step has a name - use canonical name or infer from attributes
    step_name = canonical_step.name if canonical_step.name else "Steady"
    # If name is generic, try to infer a better one
    if step_name.lower() in {"step", "steady", ""}:
        # For inference, we'll use the step_type and intensity
        # Note: We can't create a full DBWorkoutStep here since schema changed
        # Just use the step_name as-is for now
        pass

    # Build targets JSONB structure
    targets = StepTargets(
        duration=(
            DurationTime(seconds=canonical_step.duration_seconds)
            if canonical_step.duration_seconds
            else (
                DurationDistance(meters=canonical_step.distance_meters)
                if canonical_step.distance_meters
                else None
            )
        ),
        target=(
            TargetRange(metric=target_metric, min=target_min, max=target_max)
            if target_metric and target_min is not None and target_max is not None
            else (
                TargetSingleValue(metric=target_metric, value=target_value)
                if target_metric and target_value is not None
                else None
            )
        ),
    )

    return DBWorkoutStep(
        id=step_id,
        workout_id=workout_id,
        step_index=canonical_step.order,
        step_type=step_type,
        targets=targets.model_dump_jsonb(),
        instructions=step_name,
        purpose=step_name,
    )


def canonical_workout_to_db_workout(
    canonical_workout: StructuredWorkout,
    workout_id: str,
    user_id: str,
    source: str,
    raw_notes: str | None = None,
    llm_output_json: dict | None = None,
    parse_status: str | None = None,
) -> tuple[DBWorkout, list[DBWorkoutStep]]:
    """Convert canonical workout to database models.

    Args:
        canonical_workout: Canonical structured workout
        workout_id: Workout ID
        user_id: User ID
        source: Workout source (e.g., "manual")
        raw_notes: Original notes from user
        llm_output_json: LLM output JSON
        parse_status: Parse status

    Returns:
        Tuple of (Workout model, list of WorkoutStep models)
    """
    workout_name = f"{canonical_workout.sport.capitalize()} Workout"
    db_workout = DBWorkout(
        id=workout_id,
        user_id=user_id,
        sport=canonical_workout.sport,
        name=workout_name,
        description=None,
        structure=canonical_workout.model_dump() if llm_output_json is None else llm_output_json,
        tags={},
        source=source,
        source_ref=None,
        raw_notes=raw_notes,
        parse_status=parse_status,
    )

    db_steps: list[DBWorkoutStep] = []
    for canonical_step in canonical_workout.steps:
        step_id = str(uuid.uuid4())
        db_step = canonical_step_to_db_step(canonical_step, workout_id, step_id)
        db_steps.append(db_step)

    return db_workout, db_steps
