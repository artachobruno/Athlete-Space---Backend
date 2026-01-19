"""Macro plan generation (B2).

This module implements the macro plan generation step that produces
weekly focus and volume targets. This is the ONLY step that uses LLM
for planning intelligence - all downstream steps are deterministic.

Key constraints:
- Single LLM call (no retries)
- JSON schema validation mandatory
- No RAG usage
- No session generation
- No daily structure
"""

import json
from pathlib import Path

from loguru import logger
from pydantic import ValidationError
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.coach.schemas.athlete_state import AthleteState
from app.planner.enums import PlanType, WeekFocus
from app.planner.errors import InvalidMacroPlanError
from app.planner.models import MacroWeek, PlanContext
from app.planner.schemas import MacroPlanSchema
from app.planner.validators import validate_macro_plan
from app.services.llm.model import get_model


def _load_prompt() -> str:
    """Load macro plan prompt from local filesystem.

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_dir = Path(__file__).parent / "prompts"
    prompt_path = prompt_dir / "macro_plan.txt"

    if not prompt_path.exists():
        raise FileNotFoundError(f"Macro plan prompt not found: {prompt_path}")

    return prompt_path.read_text(encoding="utf-8")


def _build_llm_input(
    ctx: PlanContext, athlete_state: AthleteState
) -> dict[str, str | int | float | dict[str, float | str] | None]:
    """Build LLM input dictionary from context and athlete state.

    Args:
        ctx: Plan context
        athlete_state: Athlete state snapshot

    Returns:
        Dictionary with LLM input fields
    """
    # Build athlete summary from state
    # Map experience from CTL and volume
    ctl = athlete_state.ctl
    weekly_volume = athlete_state.seven_day_volume_hours * 60  # Convert hours to approximate km

    if ctl < 30:
        experience = "beginner"
    elif ctl < 50:
        experience = "intermediate"
    elif ctl < 70:
        experience = "trained"
    else:
        experience = "advanced"

    return {
        "plan_type": ctx.plan_type.value,
        "intent": ctx.intent.value,
        "race_distance": ctx.race_distance.value if ctx.race_distance else None,
        "weeks": ctx.weeks,
        "athlete_summary": {
            "ctl": ctl,
            "weekly_volume": weekly_volume,
            "experience": experience,
        },
    }


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

    # Load prompt
    try:
        prompt_text = _load_prompt()
    except FileNotFoundError as e:
        logger.error("Macro plan prompt not found", extra={"error": str(e)})
        raise

    # Format prompt with weeks count
    prompt_text = prompt_text.replace("{weeks}", str(ctx.weeks))

    # Build LLM input
    llm_input = _build_llm_input(ctx, athlete_state)
    input_str = json.dumps(llm_input, indent=2, default=str)

    # Create agent with schema output
    model = get_model("openai", USER_FACING_MODEL)
    agent = Agent(
        model=model,
        system_prompt=prompt_text,
        output_type=MacroPlanSchema,
    )

    # Single LLM call (no retries)
    try:
        logger.debug("Calling LLM for macro plan", input_keys=list(llm_input.keys()))
        user_prompt = f"Context:\n{input_str}"
        logger.debug(
            f"LLM Prompt: Macro Plan Generation (Planner)\n"
            f"System Prompt:\n{prompt_text}\n\n"
            f"User Prompt:\n{user_prompt}",
            system_prompt=prompt_text,
            user_prompt=user_prompt,
        )
        result = await agent.run(user_prompt)
        parsed = result.output

        logger.debug(
            "LLM returned macro plan",
            intent=parsed.intent.value,
            race_distance=parsed.race_distance.value if parsed.race_distance else None,
            week_count=len(parsed.weeks),
        )

    except ValidationError as e:
        logger.error("Macro plan schema validation failed", extra={"error": str(e)})
        raise InvalidMacroPlanError(f"Schema validation failed: {e}") from e
    except Exception as e:
        logger.error("LLM call failed", extra={"error": str(e), "error_type": type(e).__name__})
        raise RuntimeError(f"LLM call failed: {type(e).__name__}: {e}") from e

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
