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
            target_min = calc_min
            target_max = calc_max
            target_value = calc_value
    else:
        # Fallback to canonical target_type if no user settings
        if canonical_step.target_type != StepTargetType.NONE:
            target_metric = canonical_step.target_type.value

    # Ensure step has a name - use canonical name or infer from attributes
    step_name = canonical_step.name if canonical_step.name else "Steady"
    # If name is generic, try to infer a better one
    if step_name.lower() in {"step", "steady", ""}:
        # Create a temporary step-like object for inference
        # We'll use the canonical step's intensity to help infer
        temp_step = DBWorkoutStep(
            id="",
            workout_id="",
            order=0,
            type=step_type,
            intensity_zone=canonical_step.intensity.value,
            purpose=None,
            instructions=None,
        )
        step_name = infer_step_name(temp_step)

    return DBWorkoutStep(
        id=step_id,
        workout_id=workout_id,
        order=canonical_step.order,
        type=step_type,
        duration_seconds=canonical_step.duration_seconds,
        distance_meters=canonical_step.distance_meters,
        target_metric=target_metric,
        target_min=target_min,
        target_max=target_max,
        target_value=target_value,
        intensity_zone=canonical_step.intensity.value,
        instructions=step_name,
        purpose=step_name,
        inferred=False,
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
    db_workout = DBWorkout(
        id=workout_id,
        user_id=user_id,
        sport=canonical_workout.sport,
        source=source,
        source_ref=None,
        total_duration_seconds=canonical_workout.total_duration_seconds,
        total_distance_meters=canonical_workout.total_distance_meters,
        status="matched" if parse_status == "success" else "parse_failed",
        raw_notes=raw_notes,
        llm_output_json=llm_output_json,
        parse_status=parse_status,
    )

    db_steps: list[DBWorkoutStep] = []
    for canonical_step in canonical_workout.steps:
        step_id = str(uuid.uuid4())
        db_step = canonical_step_to_db_step(canonical_step, workout_id, step_id)
        db_steps.append(db_step)

    return db_workout, db_steps
