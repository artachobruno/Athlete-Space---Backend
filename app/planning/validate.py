"""Planning Invariant Validator - Core.

This validator is called before ANY calendar write.
It enforces non-negotiable planning invariants and raises PlanningInvariantError
if any invariant is violated.

📌 This replaces all retry loops.

ARCHITECTURAL COMMITMENT: TIME-BASED VALIDATION
===============================================
This validator validates TIME (minutes) as the primary planning currency.
Distance is derived from time + pace and is NOT validated directly.
"""

from app.planning.errors import PlanningInvariantError
from app.planning.invariants import (
    LONG_RUN_REQUIRED,
    LONG_RUNS_PER_WEEK,
    MAX_HARD_DAYS_PER_WEEK,
    MAX_WEEKLY_TIME_DELTA_PCT,
    MIN_EASY_DAY_GAP_BETWEEN_HARD,
)


def validate_week_plan(
    *,
    week_duration_target_minutes: int,
    day_plans: list[dict[str, str | float | int]],
    race_type: str = "default",
) -> None:
    """Validate a week plan against all planning invariants.

    Validates TIME-BASED planning (minutes) as the internal currency.
    Distance is derived from time + pace and is NOT validated here.

    Args:
        week_duration_target_minutes: Target weekly duration in minutes (int)
        day_plans: List of day plan dictionaries, each with:
            - "type": str (e.g., "easy", "long", "hard")
            - "intensity": str (e.g., "easy", "moderate", "hard")
            - "duration_minutes": int (REQUIRED - this is the primary planning currency)
        race_type: Race type for hard day limits (default: "default")

    Raises:
        PlanningInvariantError: If any invariant is violated
    """
    errors: list[str] = []

    # ---- Long run validation ----
    long_runs = [d for d in day_plans if d.get("type") == "long"]
    if LONG_RUN_REQUIRED and len(long_runs) != LONG_RUNS_PER_WEEK:
        errors.append("MISSING_LONG_RUN")

    # ---- Hard days validation ----
    hard_days = [i for i, d in enumerate(day_plans) if d.get("intensity") == "hard"]
    max_hard_days = MAX_HARD_DAYS_PER_WEEK.get(race_type, MAX_HARD_DAYS_PER_WEEK["default"])
    if len(hard_days) > max_hard_days:
        errors.append("TOO_MANY_HARD_DAYS")

    # ---- Adjacent hard days validation ----
    if len(hard_days) > 1:
        for i in range(len(hard_days) - 1):
            gap = hard_days[i + 1] - hard_days[i]
            if gap <= MIN_EASY_DAY_GAP_BETWEEN_HARD:
                errors.append("ADJACENT_HARD_DAYS")
                break

    # ---- Time-based validation (PRIMARY) ----
    # Time is the internal planning currency - distance is derived
    actual_minutes = sum(int(d.get("duration_minutes", 0)) for d in day_plans)
    tolerance_minutes = int(week_duration_target_minutes * MAX_WEEKLY_TIME_DELTA_PCT)
    if abs(actual_minutes - week_duration_target_minutes) > tolerance_minutes:
        errors.append("INVALID_WEEKLY_TIME")

    if errors:
        raise PlanningInvariantError("INVALID_WEEK", errors)
