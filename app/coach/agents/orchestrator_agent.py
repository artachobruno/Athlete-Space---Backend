"""Orchestrator Agent.

Main conversational agent that makes decisions about coaching actions.

ARCHITECTURAL INVARIANT:
The orchestrator ONLY makes decisions - it NEVER executes tools or performs side effects.
All tool execution happens in the separate executor module.
"""

from typing import cast

from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.config.models import ORCHESTRATOR_MODEL
from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
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
#
# The orchestrator now has ZERO tools - it only makes decisions.
# All tool execution happens in the executor module.


# ============================================================================
# AGENT DEFINITION
# ============================================================================

ORCHESTRATOR_AGENT_MODEL = get_model("openai", ORCHESTRATOR_MODEL)

# Agent will be initialized with instructions in run_conversation
# We need to load instructions asynchronously first
ORCHESTRATOR_AGENT: Agent[CoachDeps, OrchestratorAgentResponse] | None = None

# Agent initialization will happen in run_conversation after loading instructions
logger.info(
    "Orchestrator Agent module loaded",
    agent_name="Virtus Coach Orchestrator",
    tools=[],
)

# ============================================================================
# CONVERSATION EXECUTION
# ============================================================================


async def run_conversation(
    user_input: str,
    deps: CoachDeps,
) -> OrchestratorAgentResponse:
    """Execute conversation with orchestrator agent.

    The orchestrator ONLY makes decisions - it never executes tools.
    All execution happens in the separate executor module.

    Args:
        user_input: User's message
        deps: Dependencies with athlete state and context

    Returns:
        OrchestratorAgentResponse: Decision object with intent, horizon, action, etc.
    """
    logger.info("Starting orchestrator decision", user_input_preview=user_input[:100])

    # Load orchestrator instructions via MCP (if not already loaded)
    global ORCHESTRATOR_INSTRUCTIONS, ORCHESTRATOR_AGENT
    if not ORCHESTRATOR_INSTRUCTIONS:
        ORCHESTRATOR_INSTRUCTIONS = await _load_orchestrator_prompt()
        ORCHESTRATOR_AGENT = Agent(
            instructions=ORCHESTRATOR_INSTRUCTIONS,
            model=ORCHESTRATOR_AGENT_MODEL,
            output_type=OrchestratorAgentResponse,
            deps_type=CoachDeps,
            tools=[],  # No tools - decision only
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

    # Run agent exactly once - this produces a decision, no tool execution
    logger.debug(
        "Running orchestrator agent",
        athlete_id=deps.athlete_id,
        history_length=len(message_history),
        user_input=user_input,
    )

    # Convert dict messages to ModelMessage type for pydantic_ai
    typed_message_history = cast(list[ModelMessage], message_history) if message_history else None

    try:
        result = await ORCHESTRATOR_AGENT.run(
            user_prompt=user_input,
            deps=deps,
            message_history=typed_message_history,
        )

        # Verify response is valid and complete
        if not result.output or not result.output.message:
            logger.error(
                "Orchestrator agent returned invalid or empty response",
                athlete_id=deps.athlete_id,
            )
            return OrchestratorAgentResponse(
                intent="general",
                horizon=None,
                action="NO_ACTION",
                confidence=0.0,
                message=(
                    "I processed your request, but I'm having trouble formulating a response. Could you try rephrasing your question?"
                ),
                response_type="question",
                show_plan=False,
                plan_items=None,
                structured_data={},
                follow_up=None,
            )

        # Log usage statistics if available
        usage_info = {}
        if hasattr(result, "usage") and result.usage:
            usage_info = {
                "requests": getattr(result.usage, "requests", None),
                "total_tokens": getattr(result.usage, "total_tokens", None),
                "input_tokens": getattr(result.usage, "input_tokens", None),
                "output_tokens": getattr(result.usage, "output_tokens", None),
            }

        logger.info(
            "Orchestrator decision completed",
            athlete_id=deps.athlete_id,
            intent=result.output.intent,
            horizon=result.output.horizon,
            action=result.output.action,
            confidence=result.output.confidence,
            usage_info=usage_info,
        )

    except UsageLimitExceeded as e:
        logger.error(
            "Orchestrator agent exceeded usage limit",
            athlete_id=deps.athlete_id,
            error=str(e),
            user_input_preview=user_input[:100],
        )
        return OrchestratorAgentResponse(
            intent="general",
            horizon=None,
            action="NO_ACTION",
            confidence=0.0,
            message=(
                "I understand. Could you try rephrasing your request? "
                "I can help with training plans, activity logging, or performance analysis."
            ),
            response_type="question",
            show_plan=False,
            plan_items=None,
            structured_data={},
            follow_up=None,
        )
    except Exception as e:
        logger.error(
            "Unexpected error during orchestrator agent execution",
            athlete_id=deps.athlete_id,
            error_type=type(e).__name__,
            error=str(e),
            exc_info=True,
        )
        return OrchestratorAgentResponse(
            intent="general",
            horizon=None,
            action="NO_ACTION",
            confidence=0.0,
            message=("I encountered an issue processing your request. Please try again or rephrase your message."),
            response_type="explanation",
            show_plan=False,
            plan_items=None,
            structured_data={},
            follow_up=None,
        )

    # Log response at debug level
    logger.debug(
        "Orchestrator decision",
        intent=result.output.intent,
        horizon=result.output.horizon,
        action=result.output.action,
        confidence=result.output.confidence,
        message_length=len(result.output.message),
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
        full_response=result.output.model_dump_json(indent=2),
    )

    # Save conversation history via MCP
    # This is non-critical - conversation can continue even if context save fails
    try:
        user_message = str(user_input).strip() if user_input else ""
        if not user_message:
            logger.warning("Skipping context save: empty user message", athlete_id=deps.athlete_id)
        elif not result.output.message:
            logger.warning("Skipping context save: empty assistant message", athlete_id=deps.athlete_id)
        elif not isinstance(deps.athlete_id, int):
            logger.warning(
                f"Skipping context save: invalid athlete_id type {type(deps.athlete_id)}",
                athlete_id=deps.athlete_id,
            )
        elif not isinstance(ORCHESTRATOR_AGENT_MODEL.model_name, str):
            logger.warning(
                f"Skipping context save: invalid model_name type {type(ORCHESTRATOR_AGENT_MODEL.model_name)}",
                athlete_id=deps.athlete_id,
            )
        else:
            assistant_message = str(result.output.message).strip()
            payload = {
                "athlete_id": deps.athlete_id,
                "model_name": ORCHESTRATOR_AGENT_MODEL.model_name,
                "user_message": user_message,
                "assistant_message": assistant_message,
            }
            await call_tool("save_context", payload)
    except MCPError as e:
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
    except Exception as e:
        logger.warning(
            f"Unexpected error saving context: {type(e).__name__}: {e!s}",
            athlete_id=deps.athlete_id,
            exc_info=True,
        )

    logger.info(
        "Orchestrator decision completed",
        intent=result.output.intent,
        horizon=result.output.horizon,
        action=result.output.action,
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
    )

    return result.output
