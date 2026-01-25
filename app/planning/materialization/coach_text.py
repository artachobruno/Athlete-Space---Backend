"""Optional Coach Text Generation (Bounded LLM).

Generates optional instructional text for sessions.
LLM may ONLY write text - never numbers, distances, reps, or time changes.
Uses canonical coach vocabulary to ensure consistent language.

Allowed:
- Explain purpose
- Give pacing cues
- Provide fatigue guidance

Forbidden:
- Numbers
- Distances
- Reps
- Time changes
- Inventing new workout names
"""

import asyncio
import re

from loguru import logger
from pydantic import BaseModel
from pydantic_ai import Agent

from app.coach.vocabulary import normalize_vocabulary_level, resolve_workout_display_name
from app.planning.errors import PlanningInvariantError
from app.planning.library.session_template import SessionTemplate
from app.planning.materialization.models import ConcreteSession
from app.services.llm.model import get_model

SYSTEM_PROMPT = """You may ONLY explain the session.
You may NOT introduce numbers, durations, distances, or structure.
You may NOT modify the workout.

Your role is to provide helpful, motivational guidance about:
- Session purpose and intent
- Pacing cues and feel
- Fatigue expectations
- Execution tips

You MUST NOT include:
- Specific numbers (reps, distances, times)
- Duration changes
- Structural modifications
- Distance values

Output only plain text instructions."""


class CoachTextOutput(BaseModel):
    """LLM output schema for coach text."""

    instructions: str


async def generate_coach_text(
    session: ConcreteSession,
    template: SessionTemplate,
    philosophy_tags: list[str] | None = None,
    vocabulary_level: str | None = None,
) -> str | None:
    """Generate optional coach text for a session.

    Args:
        session: Concrete session (for context only - LLM may not change it)
        template: Session template (for intent/purpose)
        philosophy_tags: Optional philosophy tags for context
        vocabulary_level: Vocabulary level for canonical workout names (e.g. intermediate).

    Returns:
        Coach text string, or None if generation fails or is disabled

    Raises:
        PlanningInvariantError: If LLM outputs forbidden content (validated post-generation)
    """
    try:
        # Resolve canonical workout name using vocabulary system
        # Extract sport and intent from session_type and template
        session_type_lower = session.session_type.lower()

        # Map session_type to sport (default: run)
        sport = "run"
        if "ride" in session_type_lower or "bike" in session_type_lower or "cycling" in session_type_lower:
            sport = "ride"
        elif "swim" in session_type_lower:
            sport = "swim"
        elif "strength" in session_type_lower or "weight" in session_type_lower:
            sport = "strength"

        # Map session_type to intent
        intent = "easy"
        if "interval" in session_type_lower or "cruise" in session_type_lower:
            intent = "intervals"
        elif "tempo" in session_type_lower or "threshold" in session_type_lower:
            intent = "tempo"
        elif "long" in session_type_lower:
            intent = "long"
        elif "easy" in session_type_lower or "recovery" in session_type_lower:
            intent = "easy"
        elif "steady" in session_type_lower:
            intent = "steady"
        elif "rest" in session_type_lower:
            intent = "rest"

        canonical_name = resolve_workout_display_name(
            sport=sport,
            intent=intent,
            vocabulary_level=normalize_vocabulary_level(vocabulary_level),
            title=None,
        )

        # Build user prompt with canonical name
        prompt_parts = [
            f"Today's session: {canonical_name}",
            f"Session Type: {session.session_type}",
            f"Template: {template.name}",
        ]

        if template.tags:
            prompt_parts.append(f"Tags: {', '.join(template.tags)}")

        if philosophy_tags:
            prompt_parts.append(f"Philosophy: {', '.join(philosophy_tags)}")

        prompt_parts.append("")
        prompt_parts.append("Provide brief coaching instructions for this session.")
        prompt_parts.append("Focus on purpose, pacing feel, and execution guidance.")
        prompt_parts.append("Do NOT include any numbers, distances, durations, or structural details.")
        prompt_parts.append("")
        prompt_parts.append(f"IMPORTANT: Reference this session as '{canonical_name}'.")
        prompt_parts.append("Do NOT invent new workout names. Use the provided canonical name exactly.")

        user_prompt = "\n".join(prompt_parts)

        model = get_model("openai", "gpt-4o-mini")
        agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            output_type=CoachTextOutput,
        )

        logger.debug(
            "generate_coach_text: Calling LLM",
            session_type=session.session_type,
            template_id=template.id,
        )
        logger.debug(
            f"LLM Prompt: Coach Text Generation\n"
            f"System Prompt:\n{SYSTEM_PROMPT}\n\n"
            f"User Prompt:\n{user_prompt}",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        result = await agent.run(user_prompt)
        output = result.output

        if not isinstance(output, CoachTextOutput):
            logger.warning(
                "generate_coach_text: Invalid LLM output type",
                output_type=type(output).__name__,
            )
            return None

        instructions = output.instructions.strip()

        # Validate no numbers in output (basic check)
        if _contains_numbers(instructions):
            logger.warning(
                "generate_coach_text: LLM output contains numbers (filtered out)",
                instruction_preview=instructions[:100],
            )
            return None

        logger.debug(
            "generate_coach_text: Coach text generated",
            session_type=session.session_type,
            text_length=len(instructions),
        )

    except Exception as e:
        error_msg = (
            f"Failed to materialize coach text "
            f"(template_id={template.id}, "
            f"error_type={type(e).__name__})"
        )
        logger.warning(
            error_msg,
            session_type=session.session_type,
        )
        return None
    else:
        return instructions


def _contains_numbers(text: str) -> bool:
    """Check if text contains numeric values (forbidden).

    Args:
        text: Text to check

    Returns:
        True if text contains numbers, False otherwise
    """
    # Look for numeric patterns (integers, decimals, fractions)
    numeric_pattern = r"\b\d+\.?\d*\b"
    return bool(re.search(numeric_pattern, text))


def generate_coach_text_sync(
    session: ConcreteSession,
    template: SessionTemplate,
    philosophy_tags: list[str] | None = None,
    vocabulary_level: str | None = None,
) -> str | None:
    """Synchronous wrapper for generate_coach_text.

    Args:
        session: Concrete session
        template: Session template
        philosophy_tags: Optional philosophy tags
        vocabulary_level: Vocabulary level for canonical workout names (e.g. intermediate).

    Returns:
        Coach text string, or None if generation fails
    """
    try:
        return asyncio.run(generate_coach_text(session, template, philosophy_tags, vocabulary_level))
    except RuntimeError:
        # Event loop already running - return None (coach text is optional)
        logger.debug("generate_coach_text_sync: Event loop already running, skipping coach text")
        return None
