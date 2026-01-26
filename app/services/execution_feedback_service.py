"""Execution feedback service - LLM-generated coaching feedback.

LLM INPUT RULES:
- May only consume execution_summary + execution_state
- Must not infer compliance, success, or intent
- Must not invent metrics
- Must not contradict execution_state
- Output is narrative-only, not structured data
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from pydantic import BaseModel
from pydantic_ai import Agent
from sqlalchemy.orm import Session

from app.api.schemas.schemas import ExecutionStateInfo
from app.coach.config.models import USER_FACING_MODEL
from app.coach.prompts.loader import load_prompt
from app.coach.utils.llm_client import CoachLLMClient
from app.db.models import WorkoutExecutionSummary
from app.services.llm.model import get_model


class LLMFeedback(BaseModel):
    """LLM-generated coaching feedback (cached output)."""

    text: str
    tone: str  # "neutral" | "encouraging" | "corrective"
    generated_at: str  # ISO 8601 timestamp


async def generate_execution_feedback(
    execution_state: ExecutionStateInfo,
    execution_summary: WorkoutExecutionSummary,
    athlete_level: str = "intermediate",
) -> LLMFeedback | None:
    """Generate narrative feedback ONLY.

    Does not compute or infer execution state.
    Uses execution_summary + execution_state as sole inputs.

    Args:
        execution_state: Authoritative execution state (from derive_execution_state)
        execution_summary: Authoritative execution summary (from workout_execution_service)
        athlete_level: Athlete level (low | intermediate | advanced)

    Returns:
        LLMFeedback if generation succeeds, None otherwise

    Rules:
        - Only generates for executed sessions (executed_as_planned, executed_unplanned, missed)
        - Does not generate for future, cancelled, or unexecuted sessions
        - Cached output - should be stored in execution_summary.llm_feedback
    """
    # Guard: Only generate for executed sessions
    if execution_state.state not in {"executed_as_planned", "executed_unplanned", "missed"}:
        logger.debug(
            f"Skipping LLM feedback generation for state: {execution_state.state}",
            execution_state=execution_state.state,
        )
        return None

    # Guard: Must have execution summary
    if not execution_summary:
        logger.warning("Cannot generate feedback without execution_summary")
        return None

    # Guard: Check if already cached
    if execution_summary.llm_feedback:
        logger.debug("LLM feedback already cached, skipping generation")
        return None

    try:
        # Load prompt template
        prompt_text = await load_prompt("execution_feedback.txt")

        # Build context for prompt
        context = {
            "execution_state": {
                "state": execution_state.state,
                "reason": execution_state.reason,
            },
            "execution_summary": {
                "narrative": execution_summary.narrative or "No narrative available",
                "compliance_score": execution_summary.compliance_score,
                "step_comparison": execution_summary.step_comparison,
            },
            "athlete_level": athlete_level,
        }

        # Format prompt with context
        formatted_prompt = prompt_text.replace("{{ execution_state.state }}", execution_state.state)
        formatted_prompt = formatted_prompt.replace(
            "{{ execution_summary.narrative }}",
            context["execution_summary"]["narrative"],
        )
        formatted_prompt = formatted_prompt.replace(
            "{{ execution_summary.compliance_score }}",
            str(execution_summary.compliance_score) if execution_summary.compliance_score is not None else "N/A",
        )
        formatted_prompt = formatted_prompt.replace(
            "{{ execution_summary.step_comparison }}",
            str(execution_summary.step_comparison) if execution_summary.step_comparison else "N/A",
        )
        formatted_prompt = formatted_prompt.replace("{{ athlete_level }}", athlete_level)

        # Generate feedback using LLM client
        model = get_model("openai", USER_FACING_MODEL)
        agent = Agent(
            model=model,
            system_prompt=formatted_prompt,
        )

        try:
            result = await agent.run("Generate feedback based on the provided context.")
            # Extract text from result
            if hasattr(result, "output"):
                if isinstance(result.output, str):
                    feedback_text = result.output
                elif hasattr(result.output, "text"):
                    feedback_text = result.output.text
                else:
                    feedback_text = str(result.output)
            else:
                feedback_text = str(result)
        except Exception as e:
            logger.warning(f"LLM call failed for execution feedback: {e}")
            return None

        if not feedback_text or len(feedback_text.strip()) < 10:
            logger.warning("LLM generated empty or too short feedback")
            return None

        # Determine tone based on execution state and compliance
        tone = _determine_tone(execution_state.state, execution_summary.compliance_score)

        return LLMFeedback(
            text=feedback_text.strip(),
            tone=tone,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as e:
        logger.warning(f"Failed to generate execution feedback: {e}")
        # Don't raise - feedback generation is optional
        return None


def _determine_tone(
    execution_state: str,
    compliance_score: float | None,
) -> str:
    """Determine feedback tone based on execution state and compliance.

    Args:
        execution_state: Execution state string
        compliance_score: Compliance score (0.0-1.0) if available

    Returns:
        Tone string: "neutral" | "encouraging" | "corrective"
    """
    if execution_state == "missed":
        return "corrective"

    if execution_state == "executed_unplanned":
        return "neutral"

    if execution_state == "executed_as_planned" and compliance_score is not None:
        if compliance_score >= 0.8:
            return "encouraging"
        if compliance_score >= 0.6:
            return "neutral"
        return "corrective"

    return "neutral"


async def generate_and_persist_feedback_async(
    session: Session,
    execution_summary: WorkoutExecutionSummary,
    execution_state: ExecutionStateInfo,
    athlete_level: str = "intermediate",
) -> None:
    """Generate and persist LLM feedback to execution summary.

    This function generates feedback using the LLM and saves it to the
    execution summary's llm_feedback field.

    Args:
        session: Database session
        execution_summary: Workout execution summary to update
        execution_state: Execution state information
        athlete_level: Athlete level (low | intermediate | advanced)
    """
    try:
        # Generate feedback
        feedback = await generate_execution_feedback(
            execution_state=execution_state,
            execution_summary=execution_summary,
            athlete_level=athlete_level,
        )

        if feedback:
            # Convert to dict and persist
            execution_summary.llm_feedback = feedback.model_dump()
            session.commit()
            logger.info(
                "Persisted LLM feedback to execution summary",
                execution_summary_id=execution_summary.id,
                activity_id=execution_summary.activity_id,
            )
        else:
            logger.debug(
                "No feedback generated, skipping persistence",
                execution_summary_id=execution_summary.id,
            )
    except Exception as e:
        logger.warning(
            f"Failed to generate and persist feedback: {e}",
            execution_summary_id=execution_summary.id if execution_summary else None,
        )
        session.rollback()
        # Don't raise - feedback generation is optional
