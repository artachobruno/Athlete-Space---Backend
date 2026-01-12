"""Validation functions for planning data structures.

This module provides validation functions that enforce business rules
and structural constraints on planning models.
"""

from app.domains.training_plan.enums import PlanType
from app.domains.training_plan.errors import InvalidMacroPlanError, InvalidPlanContextError
from app.domains.training_plan.models import MacroWeek, PlanContext, PlannedSession


def validate_plan_context(ctx: PlanContext) -> None:
    """Validate plan context for consistency.

    Rules:
    - Race plans must have a race_distance
    - Season plans must not have a race_distance

    Args:
        ctx: Plan context to validate

    Raises:
        InvalidPlanContextError: If context violates rules
    """
    if ctx.plan_type == PlanType.RACE and ctx.race_distance is None:
        raise InvalidPlanContextError("Race plan requires race_distance")

    if ctx.plan_type == PlanType.SEASON and ctx.race_distance is not None:
        raise InvalidPlanContextError("Season plan must not specify race_distance")


def validate_macro_plan(
    weeks: list[MacroWeek],
    expected_weeks: int,
) -> None:
    """Validate macro plan structure.

    Rules:
    - Week count must match expected_weeks
    - Week indices must be sequential starting from 1

    Args:
        weeks: List of macro weeks to validate
        expected_weeks: Expected number of weeks

    Raises:
        InvalidMacroPlanError: If structure is invalid
    """
    if len(weeks) != expected_weeks:
        raise InvalidMacroPlanError(
            f"Expected {expected_weeks} weeks, got {len(weeks)}"
        )

    for i, week in enumerate(weeks, start=1):
        if week.week_index != i:
            raise InvalidMacroPlanError(
                f"Week index mismatch: expected {i}, got {week.week_index}"
            )


def validate_week_volume(
    sessions: list[PlannedSession],
    expected_total: float,
) -> None:
    """Validate that session volumes sum to expected total.

    Args:
        sessions: List of planned sessions
        expected_total: Expected total volume

    Raises:
        InvalidMacroPlanError: If volume mismatch exceeds rounding tolerance
    """
    total = round(sum(s.distance for s in sessions), 1)
    if total != round(expected_total, 1):
        raise InvalidMacroPlanError(
            f"Volume mismatch: expected {expected_total}, got {total}"
        )
