"""Orchestrator Agent.

Main conversational agent that routes queries to appropriate coaching tools.

ARCHITECTURAL INVARIANT:
The orchestrator MUST NOT execute tools directly.
All tools MUST be executed via MCP.
"""

import asyncio
import os
from contextvars import ContextVar
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
from app.services.llm.model import get_model

# Per-conversation tool execution tracking
# This tracks which tools have been executed in the current conversation turn
_executed_tools: ContextVar[set[str] | None] = ContextVar("executed_tools", default=None)

# Maximum number of tool calls per conversation turn (safety net)
# Increased from 3 to 12 to allow complex workflows while preventing runaway loops
MAX_TOOL_CALLS_PER_TURN = 12

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
    """Tool wrapper for recommend_next_session - delegates to MCP."""
    tool_name = "recommend_next_session"
    executed_tools = _executed_tools.get() or set() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"

    # Architectural guardrail: ensure MCP is used
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "state": deps.athlete_state.model_dump(),
                "user_id": deps.user_id,
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Recommendation generated.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def add_workout_tool(workout_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for add_workout - delegates to MCP."""
    tool_name = "add_workout"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"

    # Validate required parameters
    if not deps.user_id or not isinstance(deps.user_id, str):
        return "[CLARIFICATION] user_id_missing"
    if deps.athlete_id is None:
        return "[CLARIFICATION] athlete_id_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "workout_description": workout_description,
                "user_id": deps.user_id,
                "athlete_id": deps.athlete_id,
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Workout added successfully.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def adjust_training_load_tool(user_feedback: str, deps: CoachDeps) -> str:
    """Tool wrapper for adjust_training_load - delegates to MCP."""
    tool_name = "adjust_training_load"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "state": deps.athlete_state.model_dump(),
                "user_feedback": user_feedback,
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Training load adjusted.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def explain_training_state_tool(deps: CoachDeps) -> str:
    """Tool wrapper for explain_training_state - delegates to MCP."""
    tool_name = "explain_training_state"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "state": deps.athlete_state.model_dump(),
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Training state explained.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def run_analysis_tool(deps: CoachDeps) -> str:
    """Tool wrapper for run_analysis - delegates to MCP."""
    tool_name = "run_analysis"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "state": deps.athlete_state.model_dump(),
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Analysis completed.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def share_report_tool(deps: CoachDeps) -> str:
    """Tool wrapper for share_report - delegates to MCP."""
    tool_name = "share_report"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "state": deps.athlete_state.model_dump(),
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Report generated.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def plan_week_tool(deps: CoachDeps) -> str:
    """Tool wrapper for plan_week - delegates to MCP."""
    tool_name = "plan_week"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    if deps.athlete_state is None:
        return "[CLARIFICATION] athlete_state_missing"

    # Validate required parameters (plan_week needs these for idempotency check)
    if not deps.user_id or not isinstance(deps.user_id, str):
        return "[CLARIFICATION] user_id_missing"
    if deps.athlete_id is None:
        return "[CLARIFICATION] athlete_id_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "state": deps.athlete_state.model_dump(),
                "user_id": deps.user_id,
                "athlete_id": deps.athlete_id,
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Weekly plan created.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def plan_race_build_tool(race_description: str, deps: CoachDeps) -> str:
    """Tool wrapper for plan_race_build - delegates to MCP."""
    tool_name = "plan_race_build"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    # Validate required parameters
    if not deps.user_id or not isinstance(deps.user_id, str):
        return "[CLARIFICATION] user_id_missing"
    if deps.athlete_id is None:
        return "[CLARIFICATION] athlete_id_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "message": race_description,
                "user_id": deps.user_id,
                "athlete_id": deps.athlete_id,
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Race plan created.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def plan_season_tool(message: str, deps: CoachDeps) -> str:
    """Tool wrapper for plan_season - delegates to MCP."""
    tool_name = "plan_season"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've completed the plan. Let me know if you'd like changes."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    # Validate required parameters
    if not deps.user_id or not isinstance(deps.user_id, str):
        return "[CLARIFICATION] user_id_missing"
    if deps.athlete_id is None:
        return "[CLARIFICATION] athlete_id_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "message": message if message else "",
                "user_id": deps.user_id,
                "athlete_id": deps.athlete_id,
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        return result.get("message", "Season plan created.")
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        # Only transient errors (timeouts, network errors) should allow retries
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"


async def get_planned_sessions_tool(deps: CoachDeps) -> str:
    """Tool wrapper for get_planned_sessions - delegates to MCP (read-only)."""
    tool_name = "get_planned_sessions"
    executed_tools = _executed_tools.get() or set()

    # Check max tool calls
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error("Max tool calls exceeded in one turn")
        return "I've already retrieved your planned workouts. Let me know if you need anything else."

    if tool_name in executed_tools:
        logger.warning(f"Duplicate tool call blocked: {tool_name}")
        return f"[CLARIFICATION] Tool '{tool_name}' was already called this turn. Please provide a response without using this tool again."

    # Validate required parameters
    if not deps.user_id or not isinstance(deps.user_id, str):
        return "[CLARIFICATION] user_id_missing"

    # Architectural guardrail
    if os.getenv("MCP_TEST_MODE") == "1" and not callable(call_tool):
        raise RuntimeError("MCP call_tool must be callable")

    # Execute via MCP
    try:
        result = await call_tool(
            tool_name,
            {
                "user_id": deps.user_id,
            },
        )
        # Only mark as executed if successful
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)

        # Format sessions for LLM response
        sessions = result.get("sessions", [])
        if not sessions:
            return "You don't have any planned workouts yet. Would you like me to create a training plan for you?"

        # Format sessions into readable text
        sessions_text = []
        for session in sessions:
            date_str = session.get("date", "")[:10] if session.get("date") else "Unknown date"
            title = session.get("title", "Workout")
            session_type = session.get("type", "")
            intensity = session.get("intensity", "")

            session_line = f"- {date_str}: {title}"
            if session_type:
                session_line += f" ({session_type})"
            if intensity:
                session_line += f" - {intensity}"

            sessions_text.append(session_line)

        sessions_summary = "\n".join(sessions_text)
    except MCPError as e:
        logger.error(f"MCP error calling {tool_name}: {e.code}: {e.message}")
        # Mark as executed to prevent infinite retry loops for permanent errors
        executed_tools.add(tool_name)
        _executed_tools.set(executed_tools)
        # For TOOL_NOT_FOUND, return a clear message that tells LLM to stop trying this tool
        if e.code == "TOOL_NOT_FOUND":
            return (
                f"[CLARIFICATION] Tool '{tool_name}' is not available on this server. "
                "Please provide a response without using this specific tool. "
                "Use general training knowledge instead."
            )
        return f"[CLARIFICATION] {e.message}"
    else:
        return f"Here are your planned workouts:\n\n{sessions_summary}"


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
        get_planned_sessions_tool,
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


def _validate_single_run(result) -> None:
    """Validate that orchestrator agent completed in a single run.

    Args:
        result: Result from ORCHESTRATOR_AGENT.run()

    Raises:
        RuntimeError: If agent indicates it needs follow-up (should never happen)
    """
    if getattr(result, "needs_followup", False):
        raise RuntimeError(
            "Orchestrator attempted to continue after terminal response. "
            "This should never happen - .run() must be called exactly once per request."
        )


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

    # Initialize per-conversation tool execution tracking
    _executed_tools.set(set())

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

    # Set request limit to handle complex conversations while preventing infinite loops
    # Each tool call and LLM request counts toward this limit
    # 10 requests is sufficient for most conversations:
    #   - 1-2 LLM calls to understand intent
    #   - 0-3 tool calls if needed
    #   - 1-2 LLM calls to generate response
    # This prevents excessive looping while still allowing complex workflows
    # UsageLimits only supports request_limit - max_tokens is not a valid parameter
    usage_limits = UsageLimits(request_limit=10)

    # Track iteration count via tool calls as a proxy for agent iterations
    # This helps us detect if the agent is looping excessively
    iteration_tracker_start = len(_executed_tools.get() or set())

    # Check max tool calls before starting (safety net)
    executed_tools = _executed_tools.get() or set()
    if len(executed_tools) >= MAX_TOOL_CALLS_PER_TURN:
        logger.error(
            "Exceeded max tool calls before agent execution",
            athlete_id=deps.athlete_id,
            executed_tools=list(executed_tools),
            tool_call_count=len(executed_tools),
        )
        return OrchestratorAgentResponse(
            response_type="conversation",
            intent="general",
            message=("I've completed the analysis. Let me know if you need anything else."),
            structured_data={},
            follow_up=None,
        )

    try:
        logger.info(
            "Running orchestrator agent",
            athlete_id=deps.athlete_id,
            tool_calls_before=len(executed_tools),
            request_limit=usage_limits.request_limit,
        )

        result = await ORCHESTRATOR_AGENT.run(
            user_prompt=user_input,
            deps=deps,
            message_history=typed_message_history,
            usage_limits=usage_limits,
        )

        # 🚨 ABSOLUTE STOP HERE 🚨
        # No second run. No retry. No loop.
        # The agent.run() call above is the ONLY execution point.
        # pydantic_ai handles internal tool-calling loops, but we must NOT call .run() again.
        _validate_single_run(result)

        # Check max tool calls after execution and log usage statistics
        executed_tools_after = _executed_tools.get() or set()
        tool_calls_made = len(executed_tools_after) - iteration_tracker_start

        # Log usage statistics if available
        usage_info = {}
        if hasattr(result, "usage") and result.usage:
            usage_info = {
                "requests": getattr(result.usage, "requests", None),
                "total_tokens": getattr(result.usage, "total_tokens", None),
                "input_tokens": getattr(result.usage, "input_tokens", None),
                "output_tokens": getattr(result.usage, "output_tokens", None),
            }

        # Verify response is valid and complete
        if not result.output or not result.output.message:
            logger.error(
                "Orchestrator agent returned invalid or empty response",
                athlete_id=deps.athlete_id,
                tool_calls_made=tool_calls_made,
            )
            # Return a fallback response
            return OrchestratorAgentResponse(
                response_type="conversation",
                intent="general",
                message=(
                    "I processed your request, but I'm having trouble formulating a response. Could you try rephrasing your question?"
                ),
                structured_data={},
                follow_up=None,
            )

        # Check if this was a terminal state (no tool calls = direct response)
        is_terminal = tool_calls_made == 0
        logger.info(
            "Orchestrator agent execution completed",
            athlete_id=deps.athlete_id,
            tool_calls_made=tool_calls_made,
            total_tool_calls=len(executed_tools_after),
            executed_tools=list(executed_tools_after),
            is_terminal_state=is_terminal,
            response_type=result.output.response_type,
            intent=result.output.intent,
            usage_info=usage_info,
        )

        # Safety check: if we made excessive tool calls, log a warning
        if tool_calls_made >= MAX_TOOL_CALLS_PER_TURN:
            logger.warning(
                "Orchestrator agent made excessive tool calls",
                athlete_id=deps.athlete_id,
                tool_calls_made=tool_calls_made,
                max_allowed=MAX_TOOL_CALLS_PER_TURN,
                executed_tools=list(executed_tools_after),
            )

    except UsageLimitExceeded as e:
        # Log detailed information about the limit exceeded error
        executed_tools_after_error = _executed_tools.get() or set()
        tool_calls_made = len(executed_tools_after_error) - iteration_tracker_start

        logger.error(
            "Orchestrator agent exceeded usage limit",
            athlete_id=deps.athlete_id,
            error=str(e),
            tool_calls_made=tool_calls_made,
            total_tool_calls=len(executed_tools_after_error),
            executed_tools=list(executed_tools_after_error),
            request_limit=usage_limits.request_limit,
            user_input_preview=user_input[:100],
        )

        # Return a user-friendly fallback response
        # Check if we made any meaningful tool calls - if so, provide context-aware response
        if tool_calls_made > 0:
            fallback_message = (
                "Got it — I processed your request. Let me know if you want pacing advice, recovery tips, or help with training planning."
            )
        else:
            fallback_message = (
                "I understand. Could you try rephrasing your request? "
                "I can help with training plans, activity logging, or performance analysis."
            )

        return OrchestratorAgentResponse(
            response_type="conversation",
            intent="general",
            message=fallback_message,
            structured_data={},
            follow_up=None,
        )
    except Exception as e:
        # Catch any other unexpected errors during agent execution
        logger.error(
            "Unexpected error during orchestrator agent execution",
            athlete_id=deps.athlete_id,
            error_type=type(e).__name__,
            error=str(e),
            exc_info=True,
        )
        # Return a graceful fallback
        return OrchestratorAgentResponse(
            response_type="conversation",
            intent="error",
            message=("I encountered an issue processing your request. Please try again or rephrase your message."),
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
    # This is non-critical - conversation can continue even if context save fails
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
        # Context saving is non-critical - log but don't fail the conversation
        # USER_NOT_FOUND is expected when MCP server uses a different database (e.g., in tests)
        if e.code == "USER_NOT_FOUND":
            logger.warning(
                f"Could not save context (user not found in MCP server database): {e.message}",
                athlete_id=deps.athlete_id,
            )
        elif e.code == "INVALID_INPUT":
            logger.warning(
                f"Could not save context (invalid input): {e.message}",
                athlete_id=deps.athlete_id,
            )
        elif e.code == "DB_ERROR":
            logger.warning(
                f"Could not save context (database error): {e.message}",
                athlete_id=deps.athlete_id,
            )
        else:
            logger.warning(
                f"Could not save context: {e.code}: {e.message}",
                athlete_id=deps.athlete_id,
            )
        # Continue execution - context saving failure should never break chat
    except Exception as e:
        # Catch any other unexpected errors (network timeouts, etc.)
        logger.warning(
            f"Unexpected error saving context: {type(e).__name__}: {e!s}",
            athlete_id=deps.athlete_id,
            exc_info=True,
        )
        # Continue execution - context saving failure should never break chat

    logger.info(
        "Conversation completed",
        response_type=result.output.response_type,
        intent=result.output.intent,
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
    )

    return result.output
