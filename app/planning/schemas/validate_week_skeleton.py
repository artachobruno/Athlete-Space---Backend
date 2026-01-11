"""WeekSkeleton Validator.

Permanently eliminates the "0 long runs" bug class.
Validates week structure before any time allocation.
"""

from app.planning.compiler.week_skeleton import WeekSkeleton
from app.planning.errors import PlanningInvariantError
from app.planning.invariants import MAX_HARD_DAYS_PER_WEEK


def validate_week_skeleton(skeleton: WeekSkeleton, race_type: str = "default") -> None:
    """Validate a WeekSkeleton against all structural invariants.

    Args:
        skeleton: WeekSkeleton to validate
        race_type: Race type for hard day limits (default: "default")

    Raises:
        PlanningInvariantError: If any invariant is violated
    """
    roles = list(skeleton.days.values())

    # Exactly one long run required
    long_count = roles.count("long")
    if long_count != 1:
        raise PlanningInvariantError(
            "MISSING_OR_EXTRA_LONG_RUN",
            [f"Week must contain exactly one long run, got {long_count}"],
        )

    # At most MAX_HARD_DAYS_PER_WEEK hard days
    hard_count = roles.count("hard")

    max_hard_days = MAX_HARD_DAYS_PER_WEEK.get(race_type, MAX_HARD_DAYS_PER_WEEK["default"])
    if hard_count > max_hard_days:
        raise PlanningInvariantError(
            "TOO_MANY_HARD_DAYS",
            [f"Week contains {hard_count} hard days, maximum is {max_hard_days}"],
        )
