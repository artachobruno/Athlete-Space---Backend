"""Orchestrator classifier agent.

This agent classifies user intent BEFORE any tool execution.
It is a decision layer, not an intelligence layer.
"""

from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.config.models import ORCHESTRATOR_MODEL
from app.coach.prompts.loader import load_prompt
from app.coach.schemas.orchestration import OrchestrationDecision
from app.services.llm.model import get_model

# Global classifier agent (lazy loaded)
CLASSIFIER_AGENT: Agent[CoachDeps, OrchestrationDecision] | None = None
CLASSIFIER_INSTRUCTIONS = ""


async def classify_intent(
    user_input: str,
    deps: CoachDeps,
    minimal_context: dict | None = None,
) -> OrchestrationDecision:
    """Classify user intent and decide action.

    Args:
        user_input: User's message
        deps: Coach dependencies
        minimal_context: Minimal context (last plan exists, recent activity, dates)

    Returns:
        OrchestrationDecision with classification and action
    """
    global CLASSIFIER_AGENT, CLASSIFIER_INSTRUCTIONS

    # Load classifier prompt if not already loaded
    if not CLASSIFIER_INSTRUCTIONS:
        CLASSIFIER_INSTRUCTIONS = await load_prompt("orchestrator_classifier.txt")
        CLASSIFIER_AGENT = Agent(
            instructions=CLASSIFIER_INSTRUCTIONS,
            model=ORCHESTRATOR_MODEL,
            output_type=OrchestrationDecision,
            deps_type=CoachDeps,
            name="Orchestrator Classifier",
        )

    if CLASSIFIER_AGENT is None:
        raise RuntimeError("CLASSIFIER_AGENT failed to initialize")

    # Build context string for classifier
    context_parts = []
    if minimal_context:
        if minimal_context.get("last_plan_exists"):
            context_parts.append("Last plan exists: yes")
        else:
            context_parts.append("Last plan exists: no")

        if "recent_activity" in minimal_context:
            context_parts.append(f"Recent activity: {minimal_context['recent_activity']}")

        if "today_date" in minimal_context:
            context_parts.append(f"Today: {minimal_context['today_date']}")

    context_str = "\n".join(context_parts) if context_parts else "No additional context"

    # Create prompt with context
    full_prompt = f"{user_input}\n\nContext:\n{context_str}"

    logger.info(
        "Classifying user intent",
        user_input_preview=user_input[:100],
        athlete_id=deps.athlete_id,
    )

    try:
        result = await CLASSIFIER_AGENT.run(
            user_prompt=full_prompt,
            deps=deps,
        )

        decision = result.output
        logger.info(
            "Intent classified",
            intent=decision.user_intent,
            horizon=decision.horizon,
            action=decision.action,
            tool_name=decision.tool_name,
            confidence=decision.confidence,
        )
    except UsageLimitExceeded as e:
        logger.error(f"Usage limit exceeded in classifier: {e}")
        # Fallback to safe default
        return OrchestrationDecision(
            user_intent="question",
            horizon="none",
            confidence=0.0,
            action="NO_TOOL",
            tool_name="none",
            read_only=True,
            reason="Usage limit exceeded, defaulting to safe response",
        )
    except Exception as e:
        logger.exception(f"Error in classifier: {e}")
        # Fallback to safe default
        return OrchestrationDecision(
            user_intent="question",
            horizon="none",
            confidence=0.0,
            action="NO_TOOL",
            tool_name="none",
            read_only=True,
            reason=f"Classification error: {e}",
        )
    else:
        return decision
