"""PlanSpec Validator.

Ensures no plan starts malformed.
This validator enforces structural invariants on PlanSpec.
"""

from datetime import timedelta

from app.planning.errors import PlanningInvariantError
from app.planning.schemas.plan_spec import PlanSpec


def validate_plan_spec(spec: PlanSpec) -> None:
    """Validate a PlanSpec against all structural invariants.

    Args:
        spec: PlanSpec to validate

    Raises:
        PlanningInvariantError: If any invariant is violated
    """
    # Validate pace first (required field)
    if spec.assumed_pace_min_per_mile <= 0:
        raise PlanningInvariantError(
            "INVALID_PACE",
            ["assumed_pace_min_per_mile must be > 0"],
        )

    # Validate weeks calculation matches duration targets
    days_diff = (spec.end_date - spec.start_date).days
    weeks_calculated = days_diff / 7.0
    weeks_from_targets = len(spec.weekly_duration_targets_min)

    if abs(weeks_calculated - weeks_from_targets) > 1:
        raise PlanningInvariantError(
            "WEEKLY_DURATION_LENGTH_MISMATCH",
            [f"weekly_duration_targets_min length ({weeks_from_targets}) does not match date range ({weeks_calculated:.1f} weeks)"],
        )

    if not all(d > 0 for d in spec.weekly_duration_targets_min):
        raise PlanningInvariantError(
            "NON_POSITIVE_WEEKLY_DURATION",
            ["All weekly_duration_targets_min must be > 0"],
        )

    if spec.days_per_week < 4 or spec.days_per_week > 7:
        raise PlanningInvariantError(
            "INVALID_DAYS_PER_WEEK",
            [f"days_per_week ({spec.days_per_week}) must be between 4 and 7"],
        )
