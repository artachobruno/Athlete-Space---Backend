"""Macro plan LLM generation (B2 - LLM layer).

This module handles the LLM call for macro plan generation.
Domain validation and conversion happen in the domain layer.
"""

import json
from pathlib import Path

from loguru import logger
from pydantic import ValidationError
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.coach.schemas.athlete_state import AthleteState
from app.domains.training_plan.models import PlanContext
from app.domains.training_plan.schemas import MacroPlanSchema
from app.services.llm.model import get_model


def _load_prompt() -> str:
    """Load macro plan prompt from local filesystem.

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_dir = Path(__file__).parent.parent.parent / "planner" / "prompts"
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


async def generate_macro_plan_llm(
    ctx: PlanContext,
    athlete_state: AthleteState,
) -> MacroPlanSchema:
    """Generate macro plan schema via LLM.

    This function:
    - Makes ONE LLM call
    - Validates JSON schema
    - Returns parsed schema (domain layer handles conversion to models)

    Args:
        ctx: Plan context (intent, race distance, weeks)
        athlete_state: Athlete state snapshot

    Returns:
        MacroPlanSchema with parsed LLM output

    Raises:
        ValidationError: If schema validation fails
        RuntimeError: If LLM call fails
        FileNotFoundError: If prompt file is missing
    """
    logger.info(
        "Calling LLM for macro plan",
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
        # Use .opt(record=False) to prevent loguru from interpreting JSON braces as format placeholders
        logger.opt(record=False).debug(
            f"LLM Prompt: Macro Plan Generation\n"
            f"System Prompt:\n{prompt_text}\n\n"
            f"User Prompt:\n{user_prompt}"
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
        raise ValidationError(f"Schema validation failed: {e}") from e
    except Exception as e:
        logger.error("LLM call failed", extra={"error": str(e), "error_type": type(e).__name__})
        raise RuntimeError(f"LLM call failed: {type(e).__name__}: {e}") from e
    else:
        return parsed
