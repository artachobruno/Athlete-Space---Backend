"""Style LLM for rewriting structured coaching decisions into natural messages.

This LLM is NON-AUTHORITATIVE.
It may rewrite, but never decide, compute, retrieve, or execute.
"""

from loguru import logger
from pydantic import BaseModel
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.responses.prompts import STYLE_LLM_SYSTEM_PROMPT, STYLE_LLM_USER_PROMPT, StyleLLMInput
from app.responses.validator import validate_message
from app.services.llm.model import get_model


class StyleLLMOutput(BaseModel):
    """Simple output schema for Style LLM - just text."""

    message: str


def _raise_type_error(actual_type: str) -> None:
    """Raise TypeError for invalid output type."""
    logger.error(
        "Style LLM: Invalid output type",
        expected_type="StyleLLMOutput",
        actual_type=actual_type,
    )
    raise TypeError(f"Invalid LLM output type: {actual_type}")


async def generate_coach_message(structured_input: StyleLLMInput) -> str:
    """Rewrite structured coaching output into a short conversational message.

    Args:
        structured_input: Structured input with goal, headline, situation, signal, action, next

    Returns:
        Rewritten message as natural coach text

    Raises:
        ValueError: If validation fails
        RuntimeError: If LLM call fails
    """
    # Format user prompt - handle optional headline
    headline_section = f"Headline: {structured_input['headline']}\n" if structured_input.get("headline") else ""
    prompt = STYLE_LLM_USER_PROMPT.format(
        goal=structured_input["goal"],
        headline_section=headline_section,
        situation=structured_input["situation"],
        signal=structured_input["signal"],
        action=structured_input["action"],
        next=structured_input["next"],
    )

    # Create model and agent
    model = get_model("openai", USER_FACING_MODEL)
    agent = Agent(
        model=model,
        system_prompt=STYLE_LLM_SYSTEM_PROMPT,
        output_type=StyleLLMOutput,
    )

    try:
        # Call LLM
        logger.debug(
            f"LLM Prompt: Style Message Generation\n"
            f"System Prompt:\n{STYLE_LLM_SYSTEM_PROMPT}\n\n"
            f"User Prompt:\n{prompt}",
            system_prompt=STYLE_LLM_SYSTEM_PROMPT,
            user_prompt=prompt,
        )
        result = await agent.run(prompt)
        output = result.output

        if not isinstance(output, StyleLLMOutput):
            _raise_type_error(type(output).__name__)

        message = output.message.strip()

        # Validate output
        try:
            validate_message(message)
        except ValueError as e:
            logger.error(
                "Style LLM: Validation failed",
                error=str(e),
                error_type=type(e).__name__,
                prompt_preview=prompt[:200],
                model_output=message,
            )
            raise

        logger.debug(
            "Style LLM: Message generated successfully",
            message_length=len(message),
            has_headline=bool(structured_input.get("headline")),
        )
        return message
    except ValueError:
        # Re-raise validation errors (already logged above)
        raise
    except TypeError:
        # Re-raise type errors (already logged above)
        raise
    except Exception as e:
        logger.error(
            "Style LLM: Generation failed",
            error=str(e),
            error_type=type(e).__name__,
            prompt_preview=prompt[:200],
        )
        raise RuntimeError(f"Style LLM generation failed: {e}") from e
