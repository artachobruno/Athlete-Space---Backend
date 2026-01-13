"""Normalization layer for structured workouts.

This module performs non-semantic normalization only:
- Expand repeats into blocks for graphing
- Assign missing totals proportionally (if explicitly allowed)

No inference of intensity or re-interpretation of notes.
"""

from __future__ import annotations

from app.workouts.canonical import StructuredWorkout, WorkoutStep


def normalize_for_storage(
    workout: StructuredWorkout,
) -> StructuredWorkout:
    """Normalize workout for storage.

    Allowed operations:
    - Expand repeats into explicit blocks (for graphing)
    - Assign missing totals proportionally (if needed)

    Args:
        workout: Structured workout from LLM

    Returns:
        Normalized StructuredWorkout
    """
    # For now, we just return the workout as-is
    # Repeats will be expanded during graph generation
    # Missing totals are handled at validation time

    # If total_distance is missing but we have step distances, sum them
    if workout.total_distance_meters is None:
        total_step_distance = sum(
            (step.distance_meters * step.repeat) for step in workout.steps if step.distance_meters is not None
        )
        if total_step_distance > 0:
            workout.total_distance_meters = total_step_distance

    # If total_duration is missing but we have step durations, sum them
    if workout.total_duration_seconds is None:
        total_step_duration = sum(
            (step.duration_seconds * step.repeat) for step in workout.steps if step.duration_seconds is not None
        )
        if total_step_duration > 0:
            workout.total_duration_seconds = total_step_duration

    return workout


def expand_repeats(workout: StructuredWorkout) -> list[WorkoutStep]:
    """Expand repeated steps into explicit blocks.

    This is used for graphing - each repeat becomes a separate step block.

    Args:
        workout: Structured workout

    Returns:
        List of steps with repeats expanded (order updated accordingly)
    """
    expanded: list[WorkoutStep] = []
    current_order = 0

    for step in workout.steps:
        for repeat_idx in range(step.repeat):
            expanded_step = WorkoutStep(
                order=current_order,
                name=f"{step.name} (repeat {repeat_idx + 1}/{step.repeat})" if step.repeat > 1 else step.name,
                duration_seconds=step.duration_seconds,
                distance_meters=step.distance_meters,
                intensity=step.intensity,
                target_type=step.target_type,
                repeat=1,  # Expanded, so no longer repeated
                is_recovery=step.is_recovery,
            )
            expanded.append(expanded_step)
            current_order += 1

    return expanded
