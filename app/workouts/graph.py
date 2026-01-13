"""Graph time series generation for workouts.

This module generates time-aligned data points for frontend visualization.
Deterministic, no smoothing - pure cursor-based accumulation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.workouts.canonical import StructuredWorkout
from app.workouts.normalize import expand_repeats


class GraphPoint(BaseModel):
    """Single point in the workout graph time series."""

    time_seconds: int = Field(description="Time offset from workout start (seconds)")
    intensity: str = Field(description="Intensity level at this point")
    step_order: int = Field(description="Step order at this point")
    step_name: str = Field(description="Step name at this point")
    is_recovery: bool = Field(description="Whether this point is in a recovery step")


def build_graph_series(workout: StructuredWorkout, resolution_seconds: int = 1) -> list[GraphPoint]:
    """Build time series graph points from structured workout.

    Flattens repeats, converts distance to time (if needed), and creates
    a consistent timeline with specified resolution.

    Args:
        workout: Structured workout
        resolution_seconds: Time resolution for graph points (default: 1 second)

    Returns:
        List of GraphPoint objects ordered by time

    Raises:
        ValueError: If workout has distance-based steps without duration estimates
    """
    # Expand repeats into explicit blocks
    expanded_steps = expand_repeats(workout)

    # Build time series
    points: list[GraphPoint] = []
    cursor = 0

    for step in expanded_steps:
        # Determine step duration
        if step.duration_seconds is not None:
            step_duration = step.duration_seconds
        elif step.distance_meters is not None:
            # Estimate duration from distance (requires average pace/speed)
            # For now, we'll raise an error - this should be handled by normalization
            # or the frontend should provide pace estimates
            raise ValueError(
                f"Step {step.order} has distance but no duration. "
                "Distance-based steps require pace/speed estimates for graphing."
            )
        else:
            raise ValueError(f"Step {step.order} has neither duration nor distance")

        # Generate points for this step
        step_start = cursor
        step_end = cursor + step_duration

        current_time = step_start
        while current_time < step_end:
            points.append(
                GraphPoint(
                    time_seconds=current_time,
                    intensity=step.intensity.value,
                    step_order=step.order,
                    step_name=step.name,
                    is_recovery=step.is_recovery,
                )
            )
            current_time += resolution_seconds

        # Add final point at step end if not already added
        if current_time != step_end:
            points.append(
                GraphPoint(
                    time_seconds=step_end,
                    intensity=step.intensity.value,
                    step_order=step.order,
                    step_name=step.name,
                    is_recovery=step.is_recovery,
                )
            )

        cursor = step_end

    return points
