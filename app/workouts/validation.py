"""Hard validation layer for structured workouts.

This module enforces strict constraints on workout structure.
No auto-fixing or silent truncation - failures return 422 with explanation.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.workouts.canonical import StepIntensity, StepTargetType, StructuredWorkout, WorkoutStep

# Garmin FIT workout step limit
MAX_WORKOUT_STEPS = 50


class ValidationError(Exception):
    """Validation error with detailed message."""

    pass


def validate_structured_workout(workout: StructuredWorkout, activity_distance: int | None = None) -> None:
    """Validate structured workout against hard constraints.

    Enforced constraints:
    - Valid enum values only
    - No step has both distance & duration
    - Step orders sequential
    - Total distance ≤ activity distance (if provided)
    - Max steps ≤ Garmin limit

    Args:
        workout: Structured workout to validate
        activity_distance: Optional total activity distance for comparison

    Raises:
        ValidationError: If validation fails with detailed message
    """
    errors: list[str] = []

    # Validate sport
    if not workout.sport or not isinstance(workout.sport, str):
        errors.append("Sport must be a non-empty string")

    # Validate steps exist
    if not workout.steps:
        errors.append("Workout must have at least one step")

    # Validate step count limit
    if len(workout.steps) > MAX_WORKOUT_STEPS:
        errors.append(f"Workout exceeds maximum step limit ({MAX_WORKOUT_STEPS} steps)")

    # Validate each step
    seen_orders: set[int] = set()
    total_step_distance = 0

    for idx, step in enumerate(workout.steps):
        step_errors = _validate_step(step)
        if step_errors:
            errors.extend([f"Step {idx + 1}: {e}" for e in step_errors])

        # Check for duplicate orders
        if step.order in seen_orders:
            errors.append(f"Step {idx + 1}: Duplicate order {step.order}")
        seen_orders.add(step.order)

        # Accumulate distance
        if step.distance_meters is not None:
            total_step_distance += step.distance_meters * step.repeat

    # Validate order sequence
    if seen_orders:
        expected_orders = set(range(min(seen_orders), min(seen_orders) + len(seen_orders)))
        if seen_orders != expected_orders:
            errors.append(f"Step orders must be sequential starting from {min(seen_orders)}")

    # Validate total distance consistency
    if activity_distance is not None and total_step_distance > activity_distance:
        errors.append(
            f"Total step distance ({total_step_distance}m) exceeds activity distance ({activity_distance}m)"
        )

    if errors:
        error_msg = "; ".join(errors)
        raise ValidationError(f"Workout validation failed: {error_msg}")


def _validate_step(step: WorkoutStep) -> list[str]:
    """Validate a single workout step.

    Args:
        step: Step to validate

    Returns:
        List of error messages (empty if valid)
    """
    errors: list[str] = []

    # Validate order
    if step.order < 0:
        errors.append(f"Order must be >= 0, got {step.order}")

    # Validate name
    if not step.name or not isinstance(step.name, str):
        errors.append("Name must be a non-empty string")

    # Validate duration/distance (exactly one must be set)
    has_duration = step.duration_seconds is not None
    has_distance = step.distance_meters is not None

    if not has_duration and not has_distance:
        errors.append("Step must have either duration_seconds or distance_meters")
    if has_duration and has_distance:
        errors.append("Step cannot have both duration_seconds and distance_meters")

    # Validate duration
    if has_duration and step.duration_seconds is not None and step.duration_seconds <= 0:
        errors.append(f"duration_seconds must be > 0, got {step.duration_seconds}")

    # Validate distance
    if has_distance and step.distance_meters is not None and step.distance_meters <= 0:
        errors.append(f"distance_meters must be > 0, got {step.distance_meters}")

    # Validate intensity enum
    try:
        StepIntensity(step.intensity)
    except ValueError:
        errors.append(f"Invalid intensity: {step.intensity}. Must be one of: {[e.value for e in StepIntensity]}")

    # Validate target_type enum
    try:
        StepTargetType(step.target_type)
    except ValueError:
        errors.append(
            f"Invalid target_type: {step.target_type}. Must be one of: {[e.value for e in StepTargetType]}"
        )

    # Validate repeat
    if step.repeat < 1:
        errors.append(f"repeat must be >= 1, got {step.repeat}")

    return errors


def validate_and_raise_http(workout: StructuredWorkout, activity_distance: int | None = None) -> None:
    """Validate workout and raise HTTPException on failure.

    Args:
        workout: Structured workout to validate
        activity_distance: Optional total activity distance for comparison

    Raises:
        HTTPException: 422 with validation error details
    """
    try:
        validate_structured_workout(workout, activity_distance)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
