"""Orchestrator Agent.

Main conversational agent that makes decisions about coaching actions.

ARCHITECTURAL INVARIANT:
The orchestrator ONLY makes decisions - it NEVER executes tools or performs side effects.
All tool execution happens in the separate executor module.
"""

import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import cast

from loguru import logger
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage

from app.coach.agents.decision_bias import apply_rag_bias
from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.agents.orchestrator_state import OrchestratorState
from app.coach.config.models import ORCHESTRATOR_MODEL
from app.coach.config.prompt_versions import ORCHESTRATOR_PROMPT_VERSION
from app.coach.mcp_client import MCPError, call_tool
from app.coach.prompts.loader import load_prompt
from app.coach.rag.adapter import OrchestratorRagAdapter
from app.coach.rag.logging import log_rag_usage
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.coach.services.conversation_progress import create_or_update_progress, get_conversation_progress
from app.coach.tools.plan_race import parse_date_string
from app.coach.validators.execution_validator import validate_no_advice_before_execution
from app.core.attribute_extraction import extract_attributes
from app.core.observe import trace
from app.core.slot_extraction import generate_clarification_for_missing_slots
from app.core.slot_gate import REQUIRED_SLOTS, validate_slots
from app.core.token_guard import LLMMessage, enforce_token_limit
from app.core.trace_metadata import get_trace_metadata_from_deps
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

    # STEP 2: Extract attributes using authoritative extractor
    # Orchestrator decides WHAT is needed (required_attributes + optional_attributes)
    # Extractor decides WHAT is actually known
    attributes_requested = list(set(decision.required_attributes + decision.optional_attributes))

    if not attributes_requested:
        # No attributes requested - use legacy flow for backward compatibility
        # Fall back to required_slots if available
        if decision.required_slots:
            attributes_requested = decision.required_slots
        else:
            # No attributes to extract
            merged_slots = conversation_slot_state.copy()
            can_execute, missing_slots = validate_slots(tool_name, merged_slots)
            if can_execute:
                return tool_name, [], merged_slots
            return tool_name, missing_slots, merged_slots

    message_for_slots = decision.structured_data.get("race_description", "") or user_message

    # Call authoritative extractor
    extracted = await extract_attributes(
        text=message_for_slots,
        attributes_requested=attributes_requested,
        conversation_slot_state=conversation_slot_state,
    )

    # STEP 3: Normalize extractor output and merge with conversation state
    # Normalize values (convert date strings to date objects, etc.)
    normalized_slots: dict[str, str | date | int | float | bool | None] = {}
    for key, value in extracted.values.items():
        if value is None:
            continue
        # Normalize date strings to date objects
        if key == "race_date" and isinstance(value, str):
            parsed = parse_date_string(value)
            if parsed:
                normalized_slots[key] = parsed.date()
            else:
                # If parsing fails, keep as string (validation will catch it)
                normalized_slots[key] = value
        else:
            normalized_slots[key] = value

    # Merge conversation slot state + newly extracted slots (additive merge)
    # Priority: conversation_slot_state < newly extracted slots
    merged_slots = conversation_slot_state.copy()
    for key, value in normalized_slots.items():
        if value is not None:  # Only update with non-None values
            merged_slots[key] = value

    logger.info(
        "Merged slot state from extractor",
        conversation_id=conversation_id,
        previous_state=conversation_slot_state,
        extracted_values=extracted.values,
        normalized_slots=normalized_slots,
        merged_slots=merged_slots,
        missing_fields=extracted.missing_fields,
        ambiguous_fields=extracted.ambiguous_fields,
        confidence=extracted.confidence,
    )

    # STEP 4: Determine missing slots deterministically from required_attributes vs merged_slots
    # CRITICAL: Never trust extractor missing_fields - recompute deterministically
    # Missing slots = required_attributes that are NOT in merged_slots (or have None value)
    # 🔐 PHASE 0.6: This logic cannot be removed - it ensures slot recompute never trusts extractor
    missing_slots_deterministic = [
        attr
        for attr in decision.required_attributes
        if attr not in merged_slots or merged_slots.get(attr) is None
    ]

    # Also check validation gate (for backward compatibility and additional validation)
    can_execute_gate, missing_slots_gate = validate_slots(tool_name, merged_slots)

    # Combine missing slots - use deterministic computation as primary source
    # This ensures we never execute with empty filled_slots when required_attributes exist
    missing_slots = list(set(missing_slots_deterministic + missing_slots_gate))
    can_execute = len(missing_slots) == 0 and can_execute_gate

    logger.debug(
        "Slot completeness check",
        tool=tool_name,
        required_attributes=decision.required_attributes,
        merged_slots_keys=list(merged_slots.keys()),
        missing_slots_deterministic=missing_slots_deterministic,
        missing_slots_gate=missing_slots_gate,
        missing_slots=missing_slots,
        can_execute=can_execute,
    )

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
        except Exception:
            logger.exception(
                f"Failed to persist slot state (conversation_id={conversation_id})"
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
# RAG ADAPTER INITIALIZATION
# ============================================================================

# Lazy initialization of RAG adapter
_RAG_ADAPTER: OrchestratorRagAdapter | None = None


def _get_rag_adapter() -> OrchestratorRagAdapter | None:
    """Get or create RAG adapter (lazy initialization).

    Returns:
        RAG adapter or None if RAG is not available
    """
    global _RAG_ADAPTER

    if _RAG_ADAPTER is not None:
        return _RAG_ADAPTER

    try:
        # Load RAG adapter from pre-computed artifacts
        project_root = Path(__file__).parent.parent.parent.parent
        artifacts_dir = project_root / "data" / "rag_artifacts"

        if not artifacts_dir.exists():
            logger.debug(
                "RAG artifacts not found, RAG features disabled",
                artifacts_dir=str(artifacts_dir),
            )
            return None

        _RAG_ADAPTER = OrchestratorRagAdapter(artifacts_dir=artifacts_dir)

    except Exception:
        logger.exception("Failed to initialize RAG adapter, RAG features disabled")
        return None
    else:
        return _RAG_ADAPTER


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
    t0 = time.monotonic()

    logger.info("Starting orchestrator decision", user_input_preview=user_input[:100])
    logger.debug(
        "Orchestrator: Starting decision process",
        user_id=deps.user_id,
        athlete_id=deps.athlete_id,
        conversation_id=conversation_id,
        user_input_length=len(user_input),
        user_input=user_input,
    )

    # Load orchestrator instructions via MCP (if not already loaded)
    global ORCHESTRATOR_INSTRUCTIONS, ORCHESTRATOR_AGENT
    if not ORCHESTRATOR_INSTRUCTIONS:
        logger.debug("Orchestrator: Loading instructions via MCP")
        ORCHESTRATOR_INSTRUCTIONS = await load_prompt("orchestrator.txt")
        logger.debug(
            "Orchestrator: Instructions loaded",
            instructions_length=len(ORCHESTRATOR_INSTRUCTIONS),
        )
        logger.debug("Orchestrator: Creating agent instance")
        ORCHESTRATOR_AGENT = Agent(
            instructions=ORCHESTRATOR_INSTRUCTIONS,
            model=ORCHESTRATOR_AGENT_MODEL,
            output_type=OrchestratorAgentResponse,
            deps_type=CoachDeps,
            tools=[],  # No tools - decision only
            name="Virtus Coach Orchestrator",
            instrument=True,
        )
        logger.debug("Orchestrator: Agent instance created")
    else:
        logger.debug(
            "Orchestrator: Instructions already loaded",
            instructions_length=len(ORCHESTRATOR_INSTRUCTIONS),
        )

    # Load conversation history via MCP
    logger.debug(
        "Orchestrator: Loading conversation history via MCP",
        athlete_id=deps.athlete_id,
        limit=20,
    )
    try:
        result = await call_tool("load_context", {"athlete_id": deps.athlete_id, "limit": 20})
        message_history = result["messages"]
        t1 = time.monotonic()
        context_load_time = t1 - t0
        logger.info(f"[PLAN] context_load={context_load_time:.1f}s")
        logger.debug(
            "Orchestrator: Conversation history loaded",
            athlete_id=deps.athlete_id,
            message_count=len(message_history) if message_history else 0,
            has_history=bool(message_history),
        )
    except MCPError as e:
        t1 = time.monotonic()
        context_load_time = t1 - t0
        logger.info(f"[PLAN] context_load={context_load_time:.1f}s")
        logger.debug(
            "Orchestrator: Failed to load context via MCP",
            athlete_id=deps.athlete_id,
            error_code=e.code,
            error_message=e.message,
        )
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
        "Orchestrator: Exact prompt sent to LLM",
        system_prompt=ORCHESTRATOR_INSTRUCTIONS,
        message_history=message_history,
        user_input=user_input,
        full_prompt=full_prompt_text,
    )
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
    logger.debug(
        "Orchestrator: Converting message history to LLMMessage format",
        original_count=len(message_history) if message_history else 0,
    )
    llm_history: list[LLMMessage] = []
    if message_history:
        for msg in message_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in {"user", "assistant", "system"} and content:
                llm_history.append({"role": role, "content": content})
    logger.debug(
        "Orchestrator: Message history converted",
        llm_history_count=len(llm_history),
        roles=[msg["role"] for msg in llm_history],
    )

    # Build full prompt: system + history + user
    logger.debug("Orchestrator: Building full prompt")
    system_message: LLMMessage = {"role": "system", "content": ORCHESTRATOR_INSTRUCTIONS}
    user_message: LLMMessage = {"role": "user", "content": user_input}
    full_prompt: list[LLMMessage] = [system_message, *llm_history, user_message]
    logger.debug(
        "Orchestrator: Full prompt built",
        total_messages=len(full_prompt),
        system_length=len(ORCHESTRATOR_INSTRUCTIONS),
        history_length=len(llm_history),
        user_length=len(user_input),
    )

    # Apply token guard (truncates history, preserves system and user)
    logger.debug(
        "Orchestrator: Applying token guard",
        conversation_id=conversation_id_for_tokens,
        user_id=user_id,
        prompt_length=len(full_prompt),
    )
    truncated_prompt, truncation_meta = enforce_token_limit(
        full_prompt,
        conversation_id=conversation_id_for_tokens,
        user_id=user_id,
    )
    logger.debug(
        "Orchestrator: Token guard applied",
        truncated=truncation_meta["truncated"],
        removed_count=truncation_meta.get("removed_count", 0),
        original_tokens=truncation_meta.get("original_tokens", 0),
        final_tokens=truncation_meta.get("final_tokens", 0),
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
    typed_message_history: list[LLMMessage] | None = None
    if truncated_history:
        typed_message_history = truncated_history
        logger.debug(
            "Orchestrator: Converted truncated history to ModelMessage format",
            history_count=len(typed_message_history),
        )
    else:
        logger.debug("Orchestrator: No truncated history to convert")

    logger.debug(
        "Orchestrator: Calling LLM agent",
        model=model_name,
        has_history=bool(typed_message_history),
        history_length=len(typed_message_history) if typed_message_history else 0,
        user_input_length=len(user_input),
    )

    # Instrument LLM call with tracing
    trace_meta = get_trace_metadata_from_deps(deps, conversation_id=conversation_id)
    trace_meta.update(
        {
            "model": model_name,
            "prompt_version": ORCHESTRATOR_PROMPT_VERSION,
        }
    )

    try:
        t2_start = time.monotonic()
        with trace(
            name="llm.orchestrator_decision",
            metadata=trace_meta,
        ):
            result = await ORCHESTRATOR_AGENT.run(
                user_prompt=user_input,
                deps=deps,
                message_history=cast(list[ModelMessage], typed_message_history) if typed_message_history else None,
            )
        t2 = time.monotonic()
        llm_generate_time = t2 - t2_start
        logger.info(f"[PLAN] llm_generate={llm_generate_time:.1f}s")

        # Log detailed result structure immediately after agent.run()
        logger.debug(
            "Orchestrator: LLM agent completed",
            has_output=bool(result.output),
            has_message=bool(result.output and result.output.message) if result.output else False,
            result_type=type(result).__name__,
            output_type=type(result.output).__name__ if result.output else "None",
            message_value=repr(result.output.message) if result.output and hasattr(result.output, "message") else None,
            message_length=(
                len(result.output.message)
                if result.output and hasattr(result.output, "message") and result.output.message
                else 0
            ),
        )

        # Print result structure to console for debugging
        if result.output:
            output_dict = result.output.model_dump() if hasattr(result.output, "model_dump") else None
            logger.info(f"\n{'=' * 80}")
            logger.info("ORCHESTRATOR AGENT RESULT (immediate after agent.run()):")
            logger.info(f"{'=' * 80}")
            logger.info(f"Result type: {type(result).__name__}")
            logger.info(f"Output type: {type(result.output).__name__}")
            logger.info(f"Output dict keys: {list(output_dict.keys()) if output_dict else 'N/A'}")
            if output_dict:
                for key, value in output_dict.items():
                    if key == "message":
                        logger.info(f"  {key}: {value!r} (length: {len(value) if value else 0})")
                    elif isinstance(value, (dict, list)) and len(str(value)) > 100:
                        logger.info(f"  {key}: {type(value).__name__} (length: {len(str(value))} chars)")
                    else:
                        logger.info(f"  {key}: {value!r}")
            logger.info(f"{'=' * 80}\n")

        # Verify response is valid and complete
        # If should_execute is True, message can be empty (executor will generate message from tool result)
        if not result.output or (not result.output.message and not result.output.should_execute):
            # Log detailed information about the result to diagnose the issue
            result_type = type(result).__name__
            output_type = type(result.output).__name__ if result.output else "None"
            result_attrs = [attr for attr in dir(result) if not attr.startswith("_")]
            output_value = str(result.output) if result.output else None
            message_value: str | None = None
            message_is_empty_string = False
            if result.output:
                if hasattr(result.output, "message"):
                    message_value = result.output.message
                    message_is_empty_string = not message_value
                else:
                    message_value = "<message attribute not found>"
                # Check all fields on output object
                output_attrs = [attr for attr in dir(result.output) if not attr.startswith("_")]
                output_dict = result.output.model_dump() if hasattr(result.output, "model_dump") else None
            else:
                message_value = None
                output_attrs = []
                output_dict = None

            # Check for any error information in the result
            has_error = hasattr(result, "error")
            error_value = result.error if has_error else None
            has_warnings = hasattr(result, "warnings")
            warnings_value = result.warnings if has_warnings else None

            # Print detailed diagnostic information to console for immediate visibility
            logger.info(f"\n{'=' * 80}")
            logger.info("ORCHESTRATOR AGENT INVALID/EMPTY RESPONSE DIAGNOSTICS:")
            logger.info(f"{'=' * 80}")
            logger.info(f"Result type: {result_type}")
            logger.info(f"Output type: {output_type}")
            logger.info(f"Output is None: {result.output is None}")
            logger.info(f"Message is None: {message_value is None}")
            logger.info(f"Message is empty string: {message_is_empty_string}")
            logger.info(f"Message value: {repr(message_value[:200]) if message_value else None}")
            logger.info(f"Message length: {len(message_value) if message_value else 0}")
            if result.output:
                logger.info(f"Output attributes: {output_attrs}")
                if output_dict:
                    logger.info(f"Output dict keys: {list(output_dict.keys())}")
                    logger.info(f"Output dict (first 1000 chars): {str(output_dict)[:1000]}")
            logger.info(f"Result attributes: {result_attrs}")
            logger.info(f"Has error: {has_error}")
            if error_value:
                logger.info(f"Error value: {str(error_value)[:500]}")
            logger.info(f"Has warnings: {has_warnings}")
            if warnings_value:
                logger.info(f"Warnings value: {str(warnings_value)[:500]}")
            logger.info(f"User input: {user_input[:100]}")
            logger.info(f"Output repr (first 500 chars): {output_value[:500] if output_value else None}")
            logger.info(f"{'=' * 80}\n")

            logger.error(
                "Orchestrator agent returned invalid or empty response",
                athlete_id=deps.athlete_id,
                result_type=result_type,
                output_type=output_type,
                output_is_none=result.output is None,
                message_is_none=message_value is None,
                message_is_empty_string=message_is_empty_string,
                message_is_none_or_empty=message_value is None or message_is_empty_string,
                message_value=message_value[:200] if message_value else None,
                message_length=len(message_value) if message_value else 0,
                output_repr=output_value[:500] if output_value else None,
                result_attributes=result_attrs,
                output_attributes=output_attrs if result.output else [],
                output_dict_keys=list(output_dict.keys()) if output_dict else [],
                has_error=has_error,
                error_value=str(error_value)[:500] if error_value else None,
                has_warnings=has_warnings,
                warnings_value=str(warnings_value)[:500] if warnings_value else None,
                user_input_preview=user_input[:100],
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
        t3_start = time.monotonic()
        conversation_id_for_slots = conversation_id or f"orchestrator_{deps.athlete_id}"
        logger.debug(
            "Orchestrator: Computing slot state",
            conversation_id=conversation_id_for_slots,
            decision_intent=result.output.intent if result.output else None,
            decision_horizon=result.output.horizon if result.output else None,
            decision_action=result.output.action if result.output else None,
        )
        next_executable_action, computed_missing_slots, merged_slots = await _compute_missing_slots_for_decision(
            decision=result.output,
            user_message=user_input,
            conversation_id=conversation_id_for_slots,
        )
        t3 = time.monotonic()
        validation_time = t3 - t3_start
        logger.info(f"[PLAN] validation={validation_time:.1f}s")
        logger.debug(
            "Orchestrator: Slot state computed",
            next_executable_action=next_executable_action,
            computed_missing_slots=computed_missing_slots,
            merged_slots_keys=list(merged_slots.keys()) if merged_slots else [],
            merged_slots_count=len(merged_slots) if merged_slots else 0,
        )

        # Set control data fields (use target_action if provided, fall back to computed)
        result.output.target_action = result.output.target_action or next_executable_action
        result.output.next_executable_action = next_executable_action  # Legacy compatibility

        # CRITICAL: Set filled_slots to merged slots (cumulative state from conversation)
        # merged_slots IS the conversation slot state that was just persisted
        result.output.filled_slots = merged_slots

        # Assertion: filled_slots must equal merged_slots (which is the conversation slot state)
        _validate_filled_slots_equality(result.output.filled_slots, merged_slots)

        # FIX 1: Recompute missing_slots as single source of truth (DO NOT trust extractor)
        # CRITICAL: Never rely on extractor's missing_fields - recompute deterministically
        # 🔐 PHASE 0.6: This logic cannot be removed - it ensures should_execute iff missing_slots == []
        # Invariant: should_execute == True iff missing_slots == []
        required = result.output.required_attributes or []
        filled = result.output.filled_slots or {}

        result.output.missing_slots = [
            r for r in required
            if r not in filled or filled[r] in (None, "", [])
        ]

        # Assertion: missing_slots must be disjoint from filled_slots keys
        filled_keys = set(result.output.filled_slots.keys())
        missing_set = set(result.output.missing_slots)
        _validate_slots_disjoint(filled_keys, missing_set)

        # FIX 2: should_execute depends on semantic completeness (missing_slots), not validation pass
        # Execution requires semantic completeness, not just validation
        result.output.should_execute = not result.output.missing_slots

        logger.debug(
            "Orchestrator: Determining should_execute",
            target_action=result.output.target_action,
            required_attributes=required,
            filled_slots_keys=list(filled.keys()),
            missing_slots=result.output.missing_slots,
            should_execute=result.output.should_execute,
        )

        # ============================================================================
        # RAG RETRIEVAL AND DECISION BIASING (Phase 3C)
        # ============================================================================
        # RAG is retrieved during decision shaping, after slots are resolved,
        # before tool selection. RAG only influences reasoning, never executes.
        orchestrator_state = OrchestratorState()

        # Retrieve RAG context for relevant intents
        if result.output.intent in {"plan", "adjust", "explain"}:
            rag_adapter = _get_rag_adapter()
            if rag_adapter is not None:
                try:
                    # Extract race_type from merged slots or structured_data
                    race_type = (
                        merged_slots.get("race_type")
                        or result.output.structured_data.get("race_type")
                        or "marathon"  # Default fallback
                    )
                    if isinstance(race_type, str):
                        race_type_str = race_type
                    else:
                        race_type_str = "marathon"

                    # Extract athlete tags from athlete state
                    athlete_tags: list[str] = []
                    if deps.athlete_state and hasattr(deps.athlete_state, "flags"):
                        athlete_tags = deps.athlete_state.flags or []

                    # Retrieve RAG context
                    rag_context = rag_adapter.retrieve_context(
                        query=user_input,
                        race_type=race_type_str,
                        athlete_tags=athlete_tags,
                    )

                    # Store in orchestrator state
                    orchestrator_state.rag_context = rag_context

                    # Apply RAG bias to decision (creates new decision, doesn't mutate original)
                    result.output = apply_rag_bias(result.output, rag_context)

                    logger.debug(
                        "RAG context retrieved and bias applied",
                        intent=result.output.intent,
                        confidence=rag_context.confidence,
                        chunk_count=len(rag_context.chunks),
                    )

                    # Log RAG usage for observability (B50, B63)
                    log_rag_usage(
                        rag_context=rag_context,
                        intent=result.output.intent,
                        athlete_id=deps.athlete_id,
                    )

                except Exception:
                    # If RAG retrieval fails, log and continue without RAG
                    logger.exception(
                        f"RAG retrieval failed, continuing without RAG bias (intent={result.output.intent})"
                    )
                    # Continue with decision unchanged
                    # Log that RAG was not used
                    log_rag_usage(
                        rag_context=None,
                        intent=result.output.intent,
                        athlete_id=deps.athlete_id,
                    )

        # If should_execute and action is NO_ACTION, override to EXECUTE
        if result.output.should_execute:
            # Assertion: should_execute requires no missing slots
            _validate_should_execute_condition(result.output.missing_slots)
            if result.output.action == "NO_ACTION" and result.output.target_action:
                logger.debug(
                    "Orchestrator: Overriding action from NO_ACTION to EXECUTE",
                    target_action=result.output.target_action,
                )
                logger.info(
                    "Slots complete - overriding to EXECUTE immediately",
                    athlete_id=deps.athlete_id,
                    target_action=result.output.target_action,
                    missing_slots=result.output.missing_slots,
                    user_input_preview=user_input[:100],
                )
                result.output.action = "EXECUTE"
        elif result.output.missing_slots and not result.output.next_question:
            # FIX 6: Ensure correct user-visible behavior when slots are missing
            # Set next_question if missing slots and not already set
            result.output.next_question = generate_clarification_for_missing_slots(
                result.output.target_action or "plan_race_build",
                result.output.missing_slots,
            )

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

    except ValidationError as e:
        validation_errors = str(e.errors()) if hasattr(e, "errors") else None
        error_msg = (
            f"Orchestrator agent validation error - "
            f"LLM response could not be parsed "
            f"(athlete_id={deps.athlete_id}, "
            f"error_type={type(e).__name__}, "
            f"validation_errors={validation_errors})"
        )
        logger.exception(error_msg)
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
        logger.exception(
            f"Unexpected error during orchestrator agent execution (athlete_id={deps.athlete_id}, error_type={type(e).__name__})"
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
            logger.warning("Skipping context save: empty user message", extra={"athlete_id": deps.athlete_id})
        elif not result.output.message:
            logger.warning("Skipping context save: empty assistant message", extra={"athlete_id": deps.athlete_id})
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
            if conversation_id is not None:
                payload["conversation_id"] = conversation_id
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
        logger.exception(
            f"Unexpected error saving context: {type(e).__name__}: {e!s} (athlete_id={deps.athlete_id})"
        )

    t_total = time.monotonic()
    total_time = t_total - t0
    logger.info(f"[PLAN] total={total_time:.1f}s")

    logger.info(
        "Orchestrator decision completed",
        intent=result.output.intent,
        horizon=result.output.horizon,
        action=result.output.action,
        has_structured_data=bool(result.output.structured_data),
        has_follow_up=bool(result.output.follow_up),
    )

    return result.output
