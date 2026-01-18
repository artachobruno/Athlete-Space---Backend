"""Workout timeline computation.

Pure function to build time-aligned workout timeline from canonical steps.
Deterministic, no inference, no smoothing - cursor-based accumulation.
"""

from __future__ import annotations

from uuid import UUID

from app.workouts.models import Workout, WorkoutStep
from app.workouts.schemas import TimelineTarget, WorkoutTimelineResponse, WorkoutTimelineSegment
from app.workouts.targets_utils import (
    get_duration_seconds,
    get_target_max,
    get_target_metric,
    get_target_min,
    get_target_value,
)

STEP_TYPE_COLORS = {
    "warmup": "blue",
    "steady": "green",
    "interval": "red",
    "recovery": "gray",
    "cooldown": "blue",
}


def build_workout_timeline(workout: Workout, steps: list[WorkoutStep]) -> WorkoutTimelineResponse:
    """Build workout timeline from workout and steps.

    Creates contiguous time-aligned segments from duration-based steps.
    Steps are sorted by step_index and accumulated using a cursor.

    Args:
        workout: Workout model instance
        steps: List of WorkoutStep model instances (must be sorted by order)

    Returns:
        WorkoutTimelineResponse with time-aligned segments

    Raises:
        ValueError: If any step has None duration_seconds (distance-based steps not supported)
    """
    segments: list[WorkoutTimelineSegment] = []
    cursor = 0

    # Sort steps by step_index to ensure correct sequence
    sorted_steps = sorted(steps, key=lambda s: s.step_index)

    for step in sorted_steps:
        # Extract target data from targets JSONB
        targets = step.targets or {}
        duration_seconds = get_duration_seconds(targets)
        target_metric = get_target_metric(targets)
        target_min = get_target_min(targets)
        target_max = get_target_max(targets)
        target_value = get_target_value(targets)

        if duration_seconds is None:
            raise ValueError("Timeline requires duration-based steps only (Phase 2)")

        start = cursor
        end = cursor + duration_seconds

        step_color = STEP_TYPE_COLORS.get(step.step_type, "gray")
        segments.append(
            WorkoutTimelineSegment(
                step_id=UUID(step.id),
                order=step.step_index,
                step_type=step.step_type,
                step_color=step_color,
                start_second=start,
                end_second=end,
                target=TimelineTarget(
                    metric=target_metric,
                    min=target_min,
                    max=target_max,
                    value=target_value,
                ),
                purpose=step.purpose,
            )
        )

        cursor = end

    return WorkoutTimelineResponse(
        workout_id=UUID(workout.id),
        total_duration_seconds=cursor,
        segments=segments,
    )
