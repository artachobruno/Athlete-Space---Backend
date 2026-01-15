"""Workout metrics validators with hard guardrails.

Enforces invariants to prevent silent corruption:
- Distance/duration consistency
- Pace must have numeric value
- Primary metric must be satisfied

NOTE: Intent validation is separate - intent is session-level, not metrics-level.
"""

from app.plans.types import WorkoutIntent, WorkoutMetrics


def validate_workout_intent(intent: str) -> None:
    """Validate workout intent.

    Enforces that intent is one of the canonical values.

    Args:
        intent: Workout intent string

    Raises:
        ValueError: If intent is not valid
    """
    if intent not in {"rest", "easy", "long", "quality"}:
        raise ValueError(f"Invalid intent: {intent}")


def validate_workout_metrics(metrics: WorkoutMetrics) -> None:
    """Validate workout metrics invariants.

    Enforces:
    - If primary is "distance", distance_miles must be set
    - If primary is "duration", duration_min must be set
    - If pace is present, pace_min_per_mile must be set

    NOTE: Intent is validated separately at session level (MaterializedSession).

    Args:
        metrics: WorkoutMetrics to validate

    Raises:
        ValueError: If invariants are violated
    """
    if metrics.primary == "distance" and metrics.distance_miles is None:
        raise ValueError("distance_miles required when primary='distance'")
    if metrics.primary == "duration" and metrics.duration_min is None:
        raise ValueError("duration_min required when primary='duration'")

    if metrics.pace and metrics.pace.pace_min_per_mile is None:
        raise ValueError("pace_min_per_mile required when pace is present")
