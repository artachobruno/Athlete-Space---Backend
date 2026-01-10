"""Orchestrator Agent.

Main conversational agent that makes decisions about coaching actions.

ARCHITECTURAL INVARIANT:
The orchestrator ONLY makes decisions - it NEVER executes tools or performs side effects.
All tool execution happens in the separate executor module.
"""

from datetime import date, datetime, timezone
from typing import cast

from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.config.models import ORCHESTRATOR_MODEL
from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.coach.services.conversation_progress import create_or_update_progress, get_conversation_progress
from app.coach.validators.execution_validator import validate_no_advice_before_execution
from app.core.slot_extraction import extract_slots_for_intent, generate_clarification_for_missing_slots
from app.core.slot_gate import REQUIRED_SLOTS, validate_slots
from app.core.token_guard import LLMMessage, enforce_token_limit
from app.services.llm.model import get_model

# ============================================================================
# SLOT COMPUTATION
# ============================================================================


def _validate_filled_slots_equality(filled_slots: dict, merged_slots: dict) -> None:
    """Validate that filled_slots equals merged_slots.

    Args:
        filled_slots: Filled slots from result
        merged_slots: Merged slots from conversation state

    Raises:
        RuntimeError: If filled_slots does not equal merged_slots
    """
    if filled_slots != merged_slots:
        raise RuntimeError(f"filled_slots ({filled_slots}) must equal merged_slots ({merged_slots})")


def _validate_slots_disjoint(filled_keys: set, missing_set: set) -> None:
    """Validate that missing_slots is disjoint from filled_slots keys.

    Args:
        filled_keys: Set of keys from filled_slots
        missing_set: Set of missing slot names

    Raises:
        RuntimeError: If missing_slots overlaps with filled_slots keys
    """
    if not missing_set.isdisjoint(filled_keys):
        raise RuntimeError(f"missing_slots ({missing_set}) must be disjoint from filled_slots keys ({filled_keys})")


def _validate_should_execute_condition(missing_slots: list) -> None:
    """Validate that should_execute=True requires empty missing_slots.

    Args:
        missing_slots: List of missing slot names

    Raises:
        RuntimeError: If missing_slots is not empty when should_execute is True
    """
    if len(missing_slots) > 0:
        raise RuntimeError(f"should_execute=True requires empty missing_slots, got {missing_slots}")


async def _compute_missing_slots_for_decision(
    decision: OrchestratorAgentResponse,
    user_message: str,
    conversation_id: str | None,
) -> tuple[str | None, list[str], dict[str, str | date | int | float | bool | None]]:
    """Compute missing_slots deterministically from decision with slot persistence.

    CRITICAL: The orchestrator must see the cumulative slot state, not just the last message.
    This function:
    1. Loads previous slot state from conversation_progress (conversation.slot_state)
    2. Extracts new slots from current message WITH conversation context
    3. Merges previous + new slots (additive, never destructive)
    4. Persists merged slots to conversation_progress
    5. Validates AFTER merge
    6. Returns merged slots for decision

    Args:
        decision: Orchestrator decision
        user_message: Original user message
        conversation_id: Optional conversation ID for context

    Returns:
        Tuple of (next_executable_action, missing_slots, merged_slots)
    """
    # Map intent/horizon to tool name (horizon must not be None)
    if decision.horizon is None:
        return None, [], {}

    intent_to_tool = {
        ("plan", "race"): "plan_race_build",
        ("plan", "week"): "plan_week",
        ("plan", "season"): "plan_season",
    }

    tool_name = intent_to_tool.get((decision.intent, decision.horizon))
    if not tool_name or tool_name not in REQUIRED_SLOTS:
        # No executable action or no slot requirements
        return None, [], {}

    # STEP 1: Load conversation slot state (single source of truth)
    conversation_slot_state: dict[str, str | date | int | float | bool | None] = {}
    if conversation_id:
        progress = get_conversation_progress(conversation_id)
        if progress and progress.slots:
            conversation_slot_state = progress.slots.copy()
            logger.debug(
                "Loaded conversation slot state",
                conversation_id=conversation_id,
                slot_state=conversation_slot_state,
            )

    # STEP 2: Extract new slots from current message WITH conversation context
    # Pass conversation slot state so extractor can use it for context-aware extraction
    message_for_slots = decision.structured_data.get("race_description", "") or user_message
    new_slots = await extract_slots_for_intent(
        intent=decision.intent,
        horizon=decision.horizon,
        message=message_for_slots,
        _structured_data=decision.structured_data,
        conversation_id=conversation_id,
        conversation_slot_state=conversation_slot_state,
    )

    # STEP 3: Merge conversation slot state + new slots (additive merge)
    # New slots override existing ones only if they have non-None values
    merged_slots = conversation_slot_state.copy()
    for key, value in new_slots.items():
        if value is not None:  # Only update with non-None values
            merged_slots[key] = value

    logger.debug(
        "Merged slot state",
        conversation_id=conversation_id,
        previous_state=conversation_slot_state,
        new_slots=new_slots,
        merged_slots=merged_slots,
    )

    # STEP 4: Validate AFTER merge (validation must happen after merge)
    can_execute, missing_slots = validate_slots(tool_name, merged_slots)

    # STEP 5: Persist merged slots and awaiting_slots to conversation_progress AFTER validation
    # This ensures conversation.slot_state is always up-to-date with correct awaiting_slots
    # B41: Lock slot state when slots are complete (awaiting_slots is empty)
    if conversation_id:
        try:
            # B41: If slots are complete (can_execute=True), lock the slot state
            if can_execute:
                # Slots are complete - lock the state
                create_or_update_progress(
                    conversation_id=conversation_id,
                    intent=decision.intent,
                    slots=merged_slots,
                    awaiting_slots=[],  # Empty awaiting_slots = locked state
                )
                logger.info(
                    "Slot state locked after validation (slots complete)",
                    conversation_id=conversation_id,
                    merged_slots=merged_slots,
                )
            else:
                # Slots are incomplete - allow updates
                create_or_update_progress(
                    conversation_id=conversation_id,
                    intent=decision.intent,
                    slots=merged_slots,
                    awaiting_slots=missing_slots,
                )
                logger.debug(
                    "Persisted merged slot state with awaiting_slots",
                    conversation_id=conversation_id,
                    merged_slots=merged_slots,
                    awaiting_slots=missing_slots,
                )
        except Exception as e:
            logger.warning(
                "Failed to persist slot state",
                conversation_id=conversation_id,
                error=str(e),
                exc_info=True,
            )

    if can_execute:
        return tool_name, [], merged_slots

    return tool_name, missing_slots, merged_slots


# ============================================================================
# EXECUTION DETECTION
# ============================================================================


def is_executable_request(msg: str) -> bool:
    """Check if user message explicitly requests execution.

    Only imperative verbs that explicitly request creation or modification
    should trigger execution. Stating goals or providing information is not execution.

    Args:
        msg: User message

    Returns:
        True if message explicitly requests execution, False otherwise
    """
    executable_verbs = [
        "create",
        "build",
        "generate",
        "make",
        "plan",
        "schedule",
    ]
    msg_lower = msg.lower()
    # Check for imperative phrasing
    return any(verb in msg_lower for verb in executable_verbs)


def is_execution_confirmation(msg: str) -> bool:
    """Check if user message explicitly confirms execution.

    Only explicit confirmation phrases should trigger execution.
    Do NOT infer confirmation from confidence or tone.

    Args:
        msg: User message

    Returns:
        True if message explicitly confirms execution, False otherwise
    """
    confirmation_phrases = [
        "yes",
        "yeah",
        "yep",
        "yup",
        "sure",
        "ok",
        "okay",
        "go ahead",
        "do it",
        "build it",
        "create it",
        "make it",
        "generate it",
        "let's do it",
        "let's go",
        "proceed",
        "start",
        "begin",
    ]
    msg_lower = msg.lower().strip()
    # Check for exact confirmation phrases (not just containing them)
    return msg_lower in confirmation_phrases or any(
        phrase in msg_lower and len(msg_lower) < 20  # Short confirmation responses
        for phrase in confirmation_phrases
    )


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
    conversation_id: str | None = None,
) -> OrchestratorAgentResponse:
    """Execute conversation with orchestrator agent.

    The orchestrator ONLY makes decisions - it never executes tools.
    All execution happens in the separate executor module.

    Args:
        user_input: User's message
        deps: Dependencies with athlete state and context
        conversation_id: Optional conversation ID for slot persistence

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
    full_prompt_text = "\n\n".join(prompt_parts)

    logger.debug(
        "Orchestrator prompt",
        prompt_length=len(full_prompt_text),
        instructions_length=len(ORCHESTRATOR_INSTRUCTIONS),
        message_history_length=len(message_history) if message_history else 0,
        user_input_length=len(user_input),
        full_prompt=full_prompt_text,
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

    # Apply token guard before LLM call (B32)
    # Build full prompt: system + history + user
    # Then truncate history if needed, preserving system and user
    # Use provided conversation_id or fall back to athlete-based ID
    conversation_id_for_tokens = conversation_id or f"orchestrator_{deps.athlete_id}"
    user_id = deps.user_id or f"athlete_{deps.athlete_id}"

    # Convert message_history to LLMMessage format
    llm_history: list[LLMMessage] = []
    if message_history:
        for msg in message_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in {"user", "assistant", "system"} and content:
                llm_history.append({"role": role, "content": content})

    # Build full prompt: system + history + user
    system_message: LLMMessage = {"role": "system", "content": ORCHESTRATOR_INSTRUCTIONS}
    user_message: LLMMessage = {"role": "user", "content": user_input}
    full_prompt: list[LLMMessage] = [system_message, *llm_history, user_message]

    # Apply token guard (truncates history, preserves system and user)
    truncated_prompt, truncation_meta = enforce_token_limit(
        full_prompt,
        conversation_id=conversation_id_for_tokens,
        user_id=user_id,
    )

    # Extract truncated history (middle messages, excluding system and user)
    truncated_history = truncated_prompt[1:-1]

    # Log truncation event
    if truncation_meta["truncated"]:
        logger.info(
            "Token guard applied to orchestrator prompt",
            conversation_id=conversation_id_for_tokens,
            athlete_id=deps.athlete_id,
            truncated=True,
            removed_count=truncation_meta["removed_count"],
            original_tokens=truncation_meta["original_tokens"],
            final_tokens=truncation_meta["final_tokens"],
            event="token_guard",
        )
    else:
        logger.info(
            "Token guard applied to orchestrator prompt",
            conversation_id=conversation_id_for_tokens,
            athlete_id=deps.athlete_id,
            truncated=False,
            final_tokens=truncation_meta["final_tokens"],
            event="token_guard",
        )

    # Convert truncated history back to dict format for pydantic_ai
    typed_message_history: list[ModelMessage] | None = None
    if truncated_history:
        typed_message_history = cast(list[ModelMessage], truncated_history)

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

        # Compute slot state deterministically with persistence
        # Use provided conversation_id or fall back to athlete-based ID
        conversation_id_for_slots = conversation_id or f"orchestrator_{deps.athlete_id}"
        next_executable_action, computed_missing_slots, merged_slots = await _compute_missing_slots_for_decision(
            decision=result.output,
            user_message=user_input,
            conversation_id=conversation_id_for_slots,
        )

        # Set control data fields (use target_action if provided, fall back to computed)
        result.output.target_action = result.output.target_action or next_executable_action
        result.output.next_executable_action = next_executable_action  # Legacy compatibility

        # CRITICAL: Set filled_slots to merged slots (cumulative state from conversation)
        # merged_slots IS the conversation slot state that was just persisted
        result.output.filled_slots = merged_slots

        # Assertion: filled_slots must equal merged_slots (which is the conversation slot state)
        _validate_filled_slots_equality(result.output.filled_slots, merged_slots)

        # Use missing_slots from computed (based on conversation slot state)
        result.output.missing_slots = computed_missing_slots

        # Assertion: missing_slots must be disjoint from filled_slots keys
        filled_keys = set(result.output.filled_slots.keys())
        missing_set = set(result.output.missing_slots)
        _validate_slots_disjoint(filled_keys, missing_set)

        # Determine should_execute: true if slots complete (missing_slots empty) AND target_action exists
        if result.output.target_action and not result.output.missing_slots:
            result.output.should_execute = True
            # Assertion: should_execute requires no missing slots
            _validate_should_execute_condition(result.output.missing_slots)
            # If should_execute and action is NO_ACTION, override to EXECUTE
            if result.output.action == "NO_ACTION":
                logger.info(
                    "Slots complete - overriding to EXECUTE immediately",
                    athlete_id=deps.athlete_id,
                    target_action=result.output.target_action,
                    missing_slots=result.output.missing_slots,
                    user_input_preview=user_input[:100],
                )
                result.output.action = "EXECUTE"
        else:
            result.output.should_execute = False

        # Slot state already persisted in _compute_missing_slots_for_decision with correct awaiting_slots
        # No need to persist again here

        # Legacy: Set execution_confirmed to match should_execute for compatibility
        result.output.execution_confirmed = result.output.should_execute

        # CRITICAL: Enforce execution rules - intent ≠ execution
        # BUT: If should_execute is True (slots complete), always execute
        if (
            not result.output.should_execute
            and result.output.intent == "plan"
            and result.output.action == "EXECUTE"
            and not is_executable_request(user_input)
        ):
            logger.info(
                "Overriding action: user stated goal but slots not complete",
                athlete_id=deps.athlete_id,
                intent=result.output.intent,
                original_action=result.output.action,
                should_execute=result.output.should_execute,
                missing_slots=result.output.missing_slots,
                user_input_preview=user_input[:100],
            )
            result.output.action = "NO_ACTION"

        # CRITICAL: Enforce advice ban guard after orchestrator output
        # If target_action exists but should_execute is False, no advice is allowed
        if result.output.target_action and not result.output.should_execute:
            is_valid, error_msg = validate_no_advice_before_execution(
                result.output.message,
                result.output.target_action,
                result.output.missing_slots,
            )
            if not is_valid:
                logger.error(
                    "Advice ban violated - replacing with next_question",
                    athlete_id=deps.athlete_id,
                    target_action=result.output.target_action,
                    missing_slots=result.output.missing_slots,
                    error=error_msg,
                    original_message=result.output.message[:100],
                )
                # Fallback to next_question if available and valid, otherwise use generic question
                if result.output.next_question:
                    result.output.message = result.output.next_question
                else:
                    # Generate a generic question from missing slots
                    result.output.message = generate_clarification_for_missing_slots(
                        result.output.target_action,
                        result.output.missing_slots,
                    )

        logger.info(
            "Orchestrator decision completed",
            athlete_id=deps.athlete_id,
            intent=result.output.intent,
            horizon=result.output.horizon,
            action=result.output.action,
            confidence=result.output.confidence,
            target_action=result.output.target_action,
            missing_slots=result.output.missing_slots,
            should_execute=result.output.should_execute,
            next_question=result.output.next_question,
            filled_slots=result.output.filled_slots,
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
        user_message_text = str(user_input).strip() if user_input else ""
        if not user_message_text:
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
                "user_message": user_message_text,
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
