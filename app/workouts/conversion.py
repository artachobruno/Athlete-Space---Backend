"""Conversion utilities between canonical and database models.

This module handles conversion between the canonical Pydantic schema
(used for LLM output and validation) and the database SQLAlchemy models.
"""

from __future__ import annotations

import uuid

from app.workouts.canonical import StepIntensity, StepTargetType, StructuredWorkout, WorkoutStep
from app.workouts.models import Workout as DBWorkout
from app.workouts.models import WorkoutStep as DBWorkoutStep


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
) -> DBWorkoutStep:
    """Convert canonical workout step to database model.

    Args:
        canonical_step: Canonical workout step
        workout_id: Workout ID
        step_id: Step ID

    Returns:
        Database WorkoutStep model
    """
    step_type = intensity_to_step_type(canonical_step.intensity)
    if canonical_step.is_recovery:
        step_type = "recovery"

    # Map target_type to target_metric
    target_metric = None
    if canonical_step.target_type != StepTargetType.NONE:
        target_metric = canonical_step.target_type.value

    return DBWorkoutStep(
        id=step_id,
        workout_id=workout_id,
        order=canonical_step.order,
        type=step_type,
        duration_seconds=canonical_step.duration_seconds,
        distance_meters=canonical_step.distance_meters,
        target_metric=target_metric,
        target_min=None,  # Not in canonical schema yet
        target_max=None,  # Not in canonical schema yet
        target_value=None,  # Not in canonical schema yet
        intensity_zone=canonical_step.intensity.value,
        instructions=canonical_step.name,
        purpose=canonical_step.name,
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
