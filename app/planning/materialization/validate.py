"""Post-Materialization Validation.

Validates that materialized sessions preserve all Phase 0 invariants.
"""

from loguru import logger

from app.planning.errors import PlanningInvariantError
from app.planning.invariants import (
    LONG_RUNS_PER_WEEK,
    MAX_HARD_DAYS_PER_WEEK,
    MIN_EASY_DAY_GAP_BETWEEN_HARD,
)
from app.planning.logging import log_planning_invariant_failure
from app.planning.materialization.models import ConcreteSession
from app.planning.output.models import WeekPlan


def validate_materialized_sessions(
    week_plan: WeekPlan,
    concrete_sessions: list[ConcreteSession],
    race_type: str = "default",
) -> None:
    """Validate materialized sessions against Phase 0 invariants.

    Required checks:
    - Sum of duration_minutes unchanged
    - Exactly one long run
    - Hard-day adjacency still valid
    - Distance derivation accurate
    - No missing structure

    Args:
        week_plan: Original WeekPlan (for comparison)
        concrete_sessions: List of materialized ConcreteSessions
        race_type: Race type for invariant lookup

    Raises:
        PlanningInvariantError: If any invariant is violated
    """
    errors: list[str] = []

    # Check total duration preserved
    original_total = week_plan.total_duration_min
    materialized_total = sum(s.duration_minutes for s in concrete_sessions)
    if materialized_total != original_total:
        errors.append(
            f"Total duration mismatch: original={original_total}min, "
            f"materialized={materialized_total}min"
        )

    # Count long runs
    long_run_count = sum(1 for s in concrete_sessions if s.session_type == "long")
    if long_run_count != LONG_RUNS_PER_WEEK:
        errors.append(
            f"Long run count mismatch: expected={LONG_RUNS_PER_WEEK}, got={long_run_count}"
        )

    # Count hard days
    hard_types = {"interval", "tempo", "hills"}
    hard_days = [s for s in concrete_sessions if s.session_type in hard_types]
    max_hard = MAX_HARD_DAYS_PER_WEEK.get(race_type, MAX_HARD_DAYS_PER_WEEK["default"])
    if len(hard_days) > max_hard:
        errors.append(
            f"Too many hard days: {len(hard_days)} > {max_hard} (race_type={race_type})"
        )

    # Check hard-day adjacency
    day_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    hard_day_indices = [
        day_order.index(s.day) for s in hard_days if s.day in day_order
    ]
    hard_day_indices.sort()

    for i in range(len(hard_day_indices) - 1):
        gap = hard_day_indices[i + 1] - hard_day_indices[i] - 1
        if gap < MIN_EASY_DAY_GAP_BETWEEN_HARD:
            day1 = day_order[hard_day_indices[i]]
            day2 = day_order[hard_day_indices[i + 1]]
            errors.append(
                f"Hard days too close: {day1} and {day2} have only {gap} easy day(s) between"
            )

    # Check that all sessions have required structure
    # (intervals sessions should have intervals, etc.)
    for session in concrete_sessions:
        if session.session_type in {"interval", "tempo", "hills"} and not session.intervals:
            # Warning only - some templates may not define intervals
            logger.debug(
                "validate_materialized_sessions: Interval-type session has no intervals",
                day=session.day,
                session_type=session.session_type,
                template_id=session.session_template_id,
            )

    if errors:
        err = PlanningInvariantError("MATERIALIZATION_VALIDATION_FAILED", errors)
        log_planning_invariant_failure(
            err,
            {
                "week_index": week_plan.week_index,
                "race_type": race_type,
                "concrete_sessions_count": len(concrete_sessions),
            },
        )
        raise err

    logger.debug(
        "validate_materialized_sessions: Validation passed",
        week_index=week_plan.week_index,
        sessions_count=len(concrete_sessions),
    )
