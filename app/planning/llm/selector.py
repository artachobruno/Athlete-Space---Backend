"""LLM Template Selector Wrapper.

Bounded LLM selection with strict validation.
All output is validated before return.
"""

from loguru import logger
from pydantic import BaseModel
from pydantic_ai import Agent

from app.planning.errors import PlanningInvariantError
from app.planning.llm.prompts import SYSTEM_PROMPT, build_selection_prompt
from app.planning.llm.schemas import WeekSelectionInput, WeekTemplateSelection
from app.services.llm.model import get_model


class TemplateSelectionOutput(BaseModel):
    """LLM output schema for template selection."""

    week_index: int
    selections: dict[str, str]


async def select_templates(
    selection_input: WeekSelectionInput,
    philosophy_summary: str | None = None,
) -> WeekTemplateSelection:
    """Select templates using LLM from pre-filtered candidates.

    Args:
        selection_input: Complete selection input with candidates
        philosophy_summary: Optional philosophy summary from RAG

    Returns:
        WeekTemplateSelection with validated selections

    Raises:
        PlanningInvariantError: If selection fails validation
        RuntimeError: If LLM call fails
    """
    logger.debug(
        "select_templates: Starting template selection",
        week_index=selection_input.week_index,
        days_count=len(selection_input.days),
    )

    # Serialize input for prompt
    days_data = [
        {
            "day": day_candidates.day,
            "role": day_candidates.role,
            "duration_minutes": day_candidates.duration_minutes,
            "candidates": day_candidates.candidate_template_ids,
        }
        for day_candidates in selection_input.days
    ]

    user_prompt = build_selection_prompt(
        week_index=selection_input.week_index,
        race_type=selection_input.race_type,
        phase=selection_input.phase,
        philosophy_id=selection_input.philosophy_id,
        philosophy_summary=philosophy_summary,
        days=days_data,
    )

    model = get_model("openai", "gpt-4o-mini")
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        output_type=TemplateSelectionOutput,
    )

    try:
        logger.debug(
            "select_templates: Calling LLM",
            week_index=selection_input.week_index,
            prompt_length=len(user_prompt),
        )
        logger.debug(
            f"LLM Prompt: Template Selection\n"
            f"System Prompt:\n{SYSTEM_PROMPT}\n\n"
            f"User Prompt:\n{user_prompt}",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        result = await agent.run(user_prompt)
        output = result.output

    except Exception as e:
        logger.error(
            "select_templates: LLM selection failed",
            f"LLM selection failed (week_index={selection_input.week_index}, error_type={type(e).__name__})"
        )
        raise PlanningInvariantError(
            "TEMPLATE_SELECTION_FAILED",
            [f"LLM selection failed: {e!s}"],
        ) from e

    if not isinstance(output, TemplateSelectionOutput):
        error_msg = f"Expected TemplateSelectionOutput, got {type(output)}"
        raise PlanningInvariantError("INVALID_LLM_OUTPUT", [error_msg])

    validated_output = output

    # Build selection object
    selection = WeekTemplateSelection(
        week_index=validated_output.week_index,
        selections=validated_output.selections,
    )

    logger.debug(
        "select_templates: LLM selection completed",
        week_index=selection.week_index,
        selections_count=len(selection.selections),
    )
    return selection
