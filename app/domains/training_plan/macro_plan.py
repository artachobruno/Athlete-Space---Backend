"""Macro plan generation (B2).

This module implements the macro plan generation step that produces
weekly focus and volume targets. LLM calls are delegated to infra layer.

Key constraints:
- Single LLM call (no retries)
- JSON schema validation mandatory
- No RAG usage
- No session generation
- No daily structure
"""

from loguru import logger
from pydantic import ValidationError

from app.coach.schemas.athlete_state import AthleteState
from app.domains.training_plan.enums import PlanType, WeekFocus
from app.domains.training_plan.errors import InvalidMacroPlanError
from app.domains.training_plan.models import MacroWeek, PlanContext
from app.domains.training_plan.validators import validate_macro_plan
from app.infra.llm.macro_plan import generate_macro_plan_llm


async def generate_macro_plan(
    ctx: PlanContext,
    athlete_state: AthleteState,
) -> list[MacroWeek]:
    """Generate macro plan with weekly focus and volume.

    This function:
    - Makes ONE LLM call
    - Validates JSON schema
    - Converts to domain models
    - Validates business rules
    - Aborts on any failure (no retries)

    Args:
        ctx: Plan context (intent, race distance, weeks)
        athlete_state: Athlete state snapshot

    Returns:
        List of MacroWeek objects (exactly ctx.weeks items)

    Raises:
        InvalidMacroPlanError: If generation or validation fails
        FileNotFoundError: If prompt file is missing
        RuntimeError: If LLM call fails
    """
    logger.info(
        "Generating macro plan",
        plan_type=ctx.plan_type.value,
        intent=ctx.intent.value,
        race_distance=ctx.race_distance.value if ctx.race_distance else None,
        weeks=ctx.weeks,
    )

    # Call LLM via infra layer
    try:
        parsed = await generate_macro_plan_llm(ctx, athlete_state)
    except ValidationError as e:
        logger.error("Macro plan schema validation failed", error=str(e))
        raise InvalidMacroPlanError(f"Schema validation failed: {e}") from e
    except Exception as e:
        logger.error("LLM call failed", error=str(e), error_type=type(e).__name__)
        raise InvalidMacroPlanError(f"LLM call failed: {type(e).__name__}: {e}") from e

    # Convert schema to domain models
    weeks = [
        MacroWeek(
            week_index=w.week,
            focus=w.focus,
            total_distance=w.total_distance,
        )
        for w in parsed.weeks
    ]

    # Validate structure (week count, sequential indices)
    validate_macro_plan(weeks=weeks, expected_weeks=ctx.weeks)

    # Validate intent matches
    if parsed.intent != ctx.intent:
        raise InvalidMacroPlanError(
            f"Intent mismatch: expected {ctx.intent.value}, got {parsed.intent.value}"
        )

    # Validate race distance matches (if applicable)
    if ctx.plan_type == PlanType.RACE:
        if parsed.race_distance != ctx.race_distance:
            raise InvalidMacroPlanError(
                f"Race distance mismatch: expected {ctx.race_distance.value if ctx.race_distance else None}, "
                f"got {parsed.race_distance.value if parsed.race_distance else None}"
            )
        # Hard check: race plans must end with taper
        if weeks[-1].focus not in {WeekFocus.TAPER, WeekFocus.RECOVERY}:
            raise InvalidMacroPlanError(
                f"Race plan must end with taper or recovery, got {weeks[-1].focus.value}"
            )
    elif ctx.plan_type == PlanType.SEASON:
        if parsed.race_distance is not None:
            raise InvalidMacroPlanError("Season plan must not have race_distance")

    # Validate all volumes are positive
    for week in weeks:
        if week.total_distance <= 0:
            raise InvalidMacroPlanError(
                f"Week {week.week_index} has non-positive distance: {week.total_distance}"
            )

    logger.info(
        "Macro plan generated successfully",
        week_count=len(weeks),
        first_focus=weeks[0].focus.value,
        last_focus=weeks[-1].focus.value,
    )

    return weeks
