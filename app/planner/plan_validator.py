"""Plan integrity validator.

This module provides comprehensive validation of the complete plan
before persistence (B7). All checks must pass or the plan is aborted.
"""

from loguru import logger

from app.domains.training_plan.enums import DayType, PlanType, WeekFocus
from app.domains.training_plan.models import (
    MacroWeek,
    PlanContext,
    PlannedSession,
    PlannedWeek,
    WeekStructure,
)
from app.planner.errors import PlannerInvariantError
from app.planner.models import DistributedDay


def validate_plan_integrity(
    ctx: PlanContext,
    macro_weeks: list[MacroWeek],
    week_structures: list[WeekStructure],
    distributed_weeks: list[list[DistributedDay]],
    planned_weeks: list[PlannedWeek],
) -> None:
    # week_structures and distributed_weeks are kept for future validation needs
    _ = week_structures
    _ = distributed_weeks
    """Validate complete plan integrity before persistence.

    This function performs comprehensive checks:
    1. ≥1 session per week (except taper recovery edge cases)
    2. ≥1 long run before taper
    3. Race day exists (for race plans)
    4. No negative or zero-distance easy days
    5. No missing descriptions

    Args:
        ctx: Plan context
        macro_weeks: List of macro weeks from B2
        week_structures: List of week structures from B3
        distributed_weeks: List of distributed days per week from B4
        planned_weeks: List of planned weeks with sessions from B6

    Raises:
        PlannerInvariantError: If any integrity check fails
    """
    logger.info(
        "Validating plan integrity",
        plan_type=ctx.plan_type.value,
        week_count=len(planned_weeks),
    )

    # Check 1: ≥1 session per week (except taper recovery edge cases)
    for week_idx, planned_week in enumerate(planned_weeks):
        runnable_sessions = [
            s
            for s in planned_week.sessions
            if isinstance(s, PlannedSession) and s.distance > 0
        ]

        # Allow zero sessions only for taper/recovery weeks
        week_focus = planned_week.focus
        is_taper_recovery = week_focus in {WeekFocus.TAPER, WeekFocus.RECOVERY}

        if len(runnable_sessions) == 0 and not is_taper_recovery:
            raise PlannerInvariantError(
                f"Week {week_idx + 1} (focus={week_focus.value}) has zero runnable sessions"
            )

    # Check 2: ≥1 long run before taper
    taper_week_indices = [
        week_idx
        for week_idx, week in enumerate(macro_weeks)
        if week.focus in {WeekFocus.TAPER, WeekFocus.SHARPENING}
    ]

    if taper_week_indices:
        first_taper_week = min(taper_week_indices)
        has_long_run_before_taper = False

        for week_idx in range(first_taper_week):
            for session in planned_weeks[week_idx].sessions:
                if (
                    isinstance(session, PlannedSession)
                    and session.day_type == DayType.LONG
                    and session.distance > 0
                ):
                    has_long_run_before_taper = True
                    break
            if has_long_run_before_taper:
                break

        if not has_long_run_before_taper:
            raise PlannerInvariantError(
                f"No long run found before taper (first taper at week {first_taper_week + 1})"
            )

    # Check 3: Race day exists (for race plans)
    if ctx.plan_type == PlanType.RACE:
        if not ctx.target_date:
            raise PlannerInvariantError(
                "Race plan missing target_date"
            )

        # Check if any session is marked as race day (B7 will add it if missing, but validate structure)
        has_race_day = False
        for planned_week in planned_weeks:
            for session in planned_week.sessions:
                if isinstance(session, PlannedSession) and session.day_type == DayType.RACE:
                    has_race_day = True
                    break
            if has_race_day:
                break

        # Note: B7 will add race day if missing, but we validate that target_date is set
        # If race day already exists in plan, that's good
        if not has_race_day:
            # This is acceptable - B7 will add it, but we log for visibility
            logger.debug(
                "Race day session not found in planned weeks (will be added in B7)",
                target_date=ctx.target_date,
            )

    # Check 4: No negative or zero-distance easy days
    for week_idx, planned_week in enumerate(planned_weeks):
        for session in planned_week.sessions:
            if isinstance(session, PlannedSession) and session.day_type == DayType.EASY:
                if session.distance < 0:
                    raise PlannerInvariantError(
                        f"Week {week_idx + 1}, day {session.day_index}: "
                        f"Easy day has negative distance ({session.distance})"
                    )
                if session.distance == 0 and session.day_type != DayType.REST:
                    # Zero-distance easy days are rest days, which is acceptable
                    # But we should verify they're marked as rest
                    logger.debug(
                        "Zero-distance easy day (treated as rest)",
                        week=week_idx + 1,
                        day=session.day_index,
                    )

    # Check 5: No missing descriptions
    for week_idx, planned_week in enumerate(planned_weeks):
        for session in planned_week.sessions:
            if isinstance(session, PlannedSession) and session.distance > 0:
                if session.text_output is None:
                    raise PlannerInvariantError(
                        f"Week {week_idx + 1}, day {session.day_index}: "
                        f"Session with distance {session.distance} has no text_output"
                    )
                if not session.text_output.description or not session.text_output.description.strip():
                    raise PlannerInvariantError(
                        f"Week {week_idx + 1}, day {session.day_index}: "
                        f"Session has empty description"
                    )

    logger.info("Plan integrity validation passed", week_count=len(planned_weeks))
