"""Orchestrator Agent.

Main conversational agent that routes queries to appropriate coaching tools.
"""

import asyncio
from pathlib import Path
from typing import cast

from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.config.models import ORCHESTRATOR_MODEL
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.coach.tools.add_workout import add_workout
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_race import plan_race_build
from app.coach.tools.plan_season import plan_season
from app.coach.tools.plan_week import plan_week
from app.coach.tools.run_analysis import run_analysis
from app.coach.tools.share_report import share_report
from app.coach.utils.context_management import load_context, save_context
from app.services.llm.model import get_model

# ============================================================================
# AGENT INSTRUCTIONS
# ============================================================================

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_orchestrator_prompt() -> str:
    """Load orchestrator prompt from file.

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    prompt_path = PROMPTS_DIR / "orchestrator.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


ORCHESTRATOR_INSTRUCTIONS = _load_orchestrator_prompt()


# ============================================================================
# TOOLS
# ============================================================================


async def recommend_next_session_tool(deps: CoachDeps) -> str:
    """Tool wrapper for recommend_next_session."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(recommend_next_session, deps.athlete_state, deps.user_id)


async def add_workout_tool(workout_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for add_workout."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(add_workout, deps.athlete_state, workout_description, deps.user_id, deps.athlete_id)


async def adjust_training_load_tool(user_feedback: str, deps: CoachDeps) -> str:
    """Tool wrapper for adjust_training_load."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(adjust_training_load, deps.athlete_state, user_feedback)


async def explain_training_state_tool(deps: CoachDeps) -> str:
    """Tool wrapper for explain_training_state."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(explain_training_state, deps.athlete_state)


async def run_analysis_tool(deps: CoachDeps) -> str:
    """Tool wrapper for run_analysis."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(run_analysis, deps.athlete_state)


async def share_report_tool(deps: CoachDeps) -> str:
    """Tool wrapper for share_report."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(share_report, deps.athlete_state)


async def plan_week_tool(deps: CoachDeps) -> str:
    """Tool wrapper for plan_week."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await asyncio.to_thread(plan_week, deps.athlete_state)


async def plan_race_build_tool(race_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for plan_race_build."""
    return await asyncio.to_thread(
        plan_race_build,
        race_description,
        deps.user_id,
        deps.athlete_id,
    )


async def plan_season_tool(message: str, deps: CoachDeps) -> str:
    """Tool wrapper for plan_season."""
    return await asyncio.to_thread(
        plan_season,
        message if message else "",
        deps.user_id,
        deps.athlete_id,
    )


# ============================================================================
# AGENT DEFINITION
# ============================================================================


def _get_orchestrator_tools() -> list:
    """Get list of tools for orchestrator."""
    return [
        recommend_next_session_tool,
        add_workout_tool,
        adjust_training_load_tool,
        explain_training_state_tool,
        run_analysis_tool,
        share_report_tool,
        plan_week_tool,
        plan_race_build_tool,
        plan_season_tool,
    ]


ORCHESTRATOR_AGENT_MODEL = get_model("openai", ORCHESTRATOR_MODEL)
ORCHESTRATOR_AGENT = Agent(
    instructions=ORCHESTRATOR_INSTRUCTIONS,
    model=ORCHESTRATOR_AGENT_MODEL,
    output_type=OrchestratorAgentResponse,
    deps_type=CoachDeps,
    tools=_get_orchestrator_tools(),
    name="Virtus Coach Orchestrator",
    instrument=True,
)


logger.info(
    "Orchestrator Agent initialized",
    agent_name="Virtus Coach Orchestrator",
    tools=[tool.__name__ for tool in _get_orchestrator_tools()],
    instructions_length=len(ORCHESTRATOR_INSTRUCTIONS),
)

# ============================================================================
# CONVERSATION EXECUTION
# ============================================================================


async def run_conversation(
    user_input: str,
    deps: CoachDeps,
) -> OrchestratorAgentResponse:
    """Execute conversation with orchestrator agent.

    Args:
        user_input: User's message
        deps: Dependencies with athlete state and context

    Returns:
        OrchestratorAgentResponse
    """
    logger.info("Starting conversation", user_input_preview=user_input[:100])

    message_history = load_context(deps.athlete_id)

    # Log LLM model being called
    model_name = ORCHESTRATOR_AGENT_MODEL.model_name
    logger.info(
        "Calling orchestrator LLM",
        model=model_name,
        provider="openai",
        athlete_id=deps.athlete_id,
    )

    # Log full prompt at debug level
    prompt_parts = [f"Instructions: {ORCHESTRATOR_INSTRUCTIONS}"]
    if message_history:
        history_text = "\n".join([f"{msg.get('role', 'unknown')}: {msg.get('content', '')}" for msg in message_history])
        prompt_parts.append(f"Message History:\n{history_text}")
    prompt_parts.append(f"User Input: {user_input}")
    full_prompt = "\n\n".join(prompt_parts)

    logger.debug(
        "Orchestrator prompt",
        prompt_length=len(full_prompt),
        instructions_length=len(ORCHESTRATOR_INSTRUCTIONS),
        message_history_length=len(message_history) if message_history else 0,
        user_input_length=len(user_input),
        full_prompt=full_prompt,
    )

    # Run agent
    logger.debug(
        "Running orchestrator agent",
        athlete_id=deps.athlete_id,
        history_length=len(message_history),
        user_input=user_input,
    )

    # Convert dict messages to ModelMessage type for pydantic_ai
    # pydantic_ai accepts dict format at runtime but type checker expects ModelMessage
    typed_message_history = cast(list[ModelMessage], message_history) if message_history else None

    # Increase request limit to handle complex conversations with multiple tool calls
    # Default is 50, which can be exceeded in complex scenarios
    usage_limits = UsageLimits(request_limit=200)

    result = await ORCHESTRATOR_AGENT.run(
        user_prompt=user_input,
        deps=deps,
        message_history=typed_message_history,
        usage_limits=usage_limits,
    )

    # Log response at debug level
    logger.debug(
        "Orchestrator response",
        response_type=result.output.response_type,
        intent=result.output.intent,
        message_length=len(result.output.message),
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
        full_response=result.output.model_dump_json(indent=2),
    )

    # Log intent decision at info level
    logger.info(
        "Orchestrator intent decision",
        intent=result.output.intent,
        response_type=result.output.response_type,
        athlete_id=deps.athlete_id,
    )

    # Save conversation history
    save_context(
        athlete_id=deps.athlete_id,
        model_name=ORCHESTRATOR_AGENT_MODEL.model_name,
        user_message=user_input,
        assistant_message=result.output.message,
    )

    logger.info(
        "Conversation completed",
        response_type=result.output.response_type,
        intent=result.output.intent,
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
    )

    return result.output
