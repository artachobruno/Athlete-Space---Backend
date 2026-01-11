"""WeekSkeletonGenerator - Structure First.

This module creates structure-only weeks with guaranteed correctness.
It defines WHAT sessions exist and WHERE they are before time allocation.
"""

from app.planning.compiler.week_skeleton import Day, DayRole, WeekSkeleton
from app.planning.library.philosophy import TrainingPhilosophy
from app.planning.schemas.plan_spec import PlanSpec

DAYS: list[Day] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def generate_week_skeletons(
    plan_spec: PlanSpec,
    philosophy: TrainingPhilosophy,
) -> list[WeekSkeleton]:
    """Generate week skeletons for all weeks in the plan.

    Creates structure-only weeks with guaranteed correctness:
    - Exactly one long run per week
    - At most max_hard_days_per_week hard days
    - Proper spacing (no adjacent hard days)
    - Days per week constraint respected

    Args:
        plan_spec: Complete planning specification
        philosophy: Training philosophy defining constraints

    Returns:
        List of WeekSkeleton objects, one per week in the plan
    """
    weeks = []
    for week_index in range(len(plan_spec.weekly_duration_targets_min)):
        days: dict[Day, DayRole] = {}

        long_day = plan_spec.preferred_long_run_day
        hard_days = ["tue", "thu"][: philosophy.max_hard_days_per_week]

        # Initialize all days as rest
        for d in DAYS:
            days[d] = "rest"

        # Assign long day first (always counts as one active day)
        days[long_day] = "long"
        active_count = 1

        # Assign hard days
        for d in DAYS:
            if d != long_day and d in hard_days and active_count < plan_spec.days_per_week:
                days[d] = "hard"
                active_count += 1

        # Fill remaining slots with easy days
        for d in DAYS:
            if d != long_day and d not in hard_days and active_count < plan_spec.days_per_week:
                days[d] = "easy"
                active_count += 1

        weeks.append(WeekSkeleton(week_index=week_index, days=days))

    return weeks
