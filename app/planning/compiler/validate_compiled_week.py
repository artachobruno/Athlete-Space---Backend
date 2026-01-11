"""Deterministic Validation - Reuse Phase 0.

This module validates compiled weeks using Phase 0 invariants.
All failures are deterministic and raise PlanningInvariantError.
"""

from app.planning.compiler.week_skeleton import Day, DayRole, WeekSkeleton
from app.planning.validate import validate_week_plan

# Day order for proper adjacent hard day validation
DAY_ORDER: list[Day] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def validate_compiled_week(
    skeleton: WeekSkeleton,
    allocation: dict[Day, int],
    weekly_target_min: int,
    race_type: str = "default",
) -> None:
    """Validate a compiled week against Phase 0 invariants.

    Converts skeleton and allocation into day_plans format
    and validates using the Phase 0 validator.

    Days must be ordered (mon-sun) for adjacent hard day checks to work.

    Args:
        skeleton: Week structure definition
        allocation: Dictionary mapping days to allocated minutes
        weekly_target_min: Target weekly duration in minutes
        race_type: Race type for hard day limits (default: "default")

    Raises:
        PlanningInvariantError: If any Phase 0 invariant is violated
    """
    day_plans = []

    # Map DayRole to intensity for validation
    role_to_intensity: dict[DayRole, str] = {
        "long": "easy",
        "hard": "hard",
        "easy": "easy",
        "rest": "easy",
    }

    # Process days in order (mon-sun) for proper adjacent hard day validation
    for d in DAY_ORDER:
        if d in allocation:
            role = skeleton.days[d]
            minutes = allocation[d]

            # Only include days with non-zero minutes in validation
            if minutes > 0:
                day_plans.append(
                    {
                        "day": d,
                        "type": role if role == "long" else "easy",
                        "intensity": role_to_intensity[role],
                        "duration_minutes": minutes,
                    }
                )

    validate_week_plan(
        day_plans=day_plans,
        week_duration_target_minutes=weekly_target_min,
        race_type=race_type,
    )
