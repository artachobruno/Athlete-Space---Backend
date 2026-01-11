"""TimeBudgetAllocator - Math Core.

This module allocates minutes per day deterministically.
Time (minutes) is the PRIMARY currency - distance is derived later.
"""

from app.planning.compiler.week_skeleton import Day, DayRole, WeekSkeleton
from app.planning.library.philosophy import TrainingPhilosophy


def allocate_week_time(
    skeleton: WeekSkeleton,
    weekly_target_min: int,
    philosophy: TrainingPhilosophy,
) -> dict[Day, int]:
    """Allocate minutes per day deterministically.

    Allocates time based on:
    - Long run gets long_run_ratio_max of weekly time (capped to ensure ratio is respected)
    - Hard days get 30% of remaining time (split equally)
    - Easy days get remaining time (split equally)
    - Rest days get 0 minutes

    Args:
        skeleton: Week structure definition
        weekly_target_min: Target weekly duration in minutes
        philosophy: Training philosophy defining constraints

    Returns:
        Dictionary mapping days to allocated minutes
    """
    allocation: dict[Day, int] = {}

    # Long run gets long_run_ratio_max, but ensure it doesn't exceed max ratio
    # Use floor to ensure ratio doesn't exceed max due to rounding
    long_ratio = philosophy.long_run_ratio_max
    long_minutes = int(weekly_target_min * long_ratio)
    # Ensure long run ratio doesn't exceed max (check after calculation)
    max_long_minutes = int(weekly_target_min * philosophy.long_run_ratio_max)
    long_minutes = min(long_minutes, max_long_minutes)

    remaining = weekly_target_min - long_minutes

    hard_days = [d for d, r in skeleton.days.items() if r == "hard"]
    easy_days = [d for d, r in skeleton.days.items() if r == "easy"]

    hard_minutes_each = int(remaining * 0.3 / max(len(hard_days), 1))
    allocated_hard = hard_minutes_each * len(hard_days)

    remaining -= allocated_hard

    easy_minutes_each = int(remaining / max(len(easy_days), 1))

    for d, role in skeleton.days.items():
        if role == "long":
            allocation[d] = long_minutes
        elif role == "hard":
            allocation[d] = hard_minutes_each
        elif role == "easy":
            allocation[d] = easy_minutes_each
        else:
            allocation[d] = 0

    return allocation
