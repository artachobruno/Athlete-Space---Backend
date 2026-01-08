"""Orchestrator Agent.

Main conversational agent that routes queries to appropriate coaching tools.
"""

import asyncio
from typing import cast

from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.config.models import ORCHESTRATOR_MODEL
from app.coach.mcp_client import MCPError, call_tool
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
from app.services.llm.model import get_model

# ============================================================================
# AGENT INSTRUCTIONS
# ============================================================================


async def _load_orchestrator_prompt() -> str:
    """Load orchestrator prompt via MCP.

    Returns:
        Prompt content as string

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    try:
        result = await call_tool("load_orchestrator_prompt", {})
        return result["content"]
    except MCPError as e:
        if e.code == "FILE_NOT_FOUND":
            raise FileNotFoundError(f"Orchestrator prompt file not found: {e.message}") from e
        raise RuntimeError(f"Failed to load orchestrator prompt: {e.message}") from e


# Load prompt synchronously at module level (will be replaced with async loading if needed)
# For now, we'll load it lazily in run_conversation
ORCHESTRATOR_INSTRUCTIONS = ""


# ============================================================================
# TOOLS
# ============================================================================


async def recommend_next_session_tool(deps: CoachDeps) -> str:
    """Tool wrapper for recommend_next_session."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await recommend_next_session(deps.athlete_state, deps.user_id)


async def add_workout_tool(workout_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for add_workout."""
    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"
    return await add_workout(deps.athlete_state, workout_description, deps.user_id, deps.athlete_id)


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
    return await plan_race_build(
        race_description,
        deps.user_id,
        deps.athlete_id,
    )


async def plan_season_tool(message: str, deps: CoachDeps) -> str:
    """Tool wrapper for plan_season."""
    return await plan_season(
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

# Agent will be initialized with instructions in run_conversation
# We need to load instructions asynchronously first
ORCHESTRATOR_AGENT: Agent[CoachDeps, OrchestratorAgentResponse] | None = None


# Agent initialization will happen in run_conversation after loading instructions
logger.info(
    "Orchestrator Agent module loaded",
    agent_name="Virtus Coach Orchestrator",
    tools=[tool.__name__ for tool in _get_orchestrator_tools()],
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

    # Load orchestrator instructions via MCP (if not already loaded)
    global ORCHESTRATOR_INSTRUCTIONS, ORCHESTRATOR_AGENT
    if not ORCHESTRATOR_INSTRUCTIONS:
        ORCHESTRATOR_INSTRUCTIONS = await _load_orchestrator_prompt()
        ORCHESTRATOR_AGENT = Agent(
            instructions=ORCHESTRATOR_INSTRUCTIONS,
            model=ORCHESTRATOR_AGENT_MODEL,
            output_type=OrchestratorAgentResponse,
            deps_type=CoachDeps,
            tools=_get_orchestrator_tools(),
            name="Virtus Coach Orchestrator",
            instrument=True,
        )

    # Load conversation history via MCP
    try:
        result = await call_tool("load_context", {"athlete_id": deps.athlete_id, "limit": 20})
        message_history = result["messages"]
    except MCPError as e:
        logger.error(f"Failed to load context: {e.code}: {e.message}")
        message_history = []

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

    # Ensure agent is initialized
    if ORCHESTRATOR_AGENT is None:
        raise RuntimeError("Orchestrator agent not initialized")

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
    # Each tool call and LLM request counts toward this limit
    usage_limits = UsageLimits(request_limit=500)

    try:
        result = await ORCHESTRATOR_AGENT.run(
            user_prompt=user_input,
            deps=deps,
            message_history=typed_message_history,
            usage_limits=usage_limits,
        )
    except UsageLimitExceeded as e:
        logger.error(
            "Orchestrator agent exceeded usage limit",
            athlete_id=deps.athlete_id,
            error=str(e),
        )
        # Return a helpful error response
        return OrchestratorAgentResponse(
            response_type="clarification",
            intent="error",
            message=(
                "I apologize, but this conversation has become too complex and exceeded my processing limits. "
                "Please try rephrasing your request or breaking it into smaller parts."
            ),
            structured_data={},
            follow_up=None,
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

    # Save conversation history via MCP
    try:
        await call_tool(
            "save_context",
            {
                "athlete_id": deps.athlete_id,
                "model_name": ORCHESTRATOR_AGENT_MODEL.model_name,
                "user_message": user_input,
                "assistant_message": result.output.message,
            },
        )
    except MCPError as e:
        logger.error(f"Failed to save context: {e.code}: {e.message}")
        # Continue execution even if save fails

    logger.info(
        "Conversation completed",
        response_type=result.output.response_type,
        intent=result.output.intent,
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
    )

    return result.output
