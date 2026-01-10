"""Slot extraction utilities for extracting structured data from user messages.

This module provides functions to extract slots (structured data) from user messages
BEFORE tool routing, enabling precondition validation.
"""

from datetime import date, datetime, timezone

from loguru import logger

from app.coach.services.conversation_progress import get_conversation_progress
from app.coach.tools.plan_race import (
    extract_race_information,
    extract_training_goal,
    parse_date_string,
)


class ToolContext:
    """Context for tool execution with extracted slots.

    Attributes:
        intent: Intent classification (e.g., "race_plan")
        slots: Dictionary of extracted slots (values may be None)
    """

    def __init__(self, intent: str, slots: dict[str, str | date | int | float | bool | None]):
        """Initialize ToolContext.

        Args:
            intent: Intent classification
            slots: Dictionary of extracted slots
        """
        self.intent = intent
        self.slots = slots


def generate_clarification_for_missing_slots(tool_name: str, missing_slots: list[str]) -> str:
    """Generate user-friendly clarification message for missing slots.

    Args:
        tool_name: Name of the tool that requires the slots
        missing_slots: List of missing slot names

    Returns:
        User-friendly clarification message
    """
    if tool_name == "plan_race_build":
        missing_items = []
        if "race_distance" in missing_slots:
            missing_items.append("Race distance (e.g., 5K, 10K, half marathon, marathon, ultra)")
        if "race_date" in missing_slots:
            missing_items.append("Race date (e.g., April 25, 2026)")

        if not missing_items:
            return "I can build your race training plan. Please provide the required information."

        items_text = "\n".join([f"• {item}" for item in missing_items])
        return (
            f"I can build your race training plan, I just need one more detail:\n\n{items_text}\n\n"
            "Once you provide that, I'll generate the full plan and add it to your calendar."
        )

    # Generic fallback
    slot_names = ", ".join(missing_slots)
    return f"I need more information to proceed: {slot_names}. Please provide these details."


async def extract_race_slots(
    message: str,
    conversation_id: str | None = None,
    conversation_slot_state: dict[str, str | date | int | float | bool | None] | None = None,
) -> dict[str, str | date | None]:
    """Extract race-related slots from user message with conversation context.

    CRITICAL: Uses conversation_slot_state for context-aware extraction:
    - If race_distance already known → extractor should not re-ask for it
    - If user says "04/25" → infer year from previously known month
    - Uses conversation-level state, not per-turn state

    Args:
        message: User message containing race details
        conversation_id: Optional conversation ID for context-aware extraction
        conversation_slot_state: Current conversation slot state (preferred over loading from DB)

    Returns:
        Dictionary with slots:
        - race_distance: str | None
        - race_date: date | None
        - target_time: str | None (optional)
    """
    # Use conversation_slot_state if provided, otherwise load from DB
    slot_state = conversation_slot_state
    if slot_state is None and conversation_id:
        progress = get_conversation_progress(conversation_id)
        if progress and progress.slots:
            slot_state = progress.slots.copy()

    # Build conversation context from slot state for extractor
    conversation_context = build_conversation_context_from_slot_state(slot_state or {})
    today = datetime.now(timezone.utc).date()

    # Determine awaiting slots from progress if available
    awaiting_slots: list[str] = []
    if conversation_id:
        progress = get_conversation_progress(conversation_id)
        if progress:
            awaiting_slots = progress.awaiting_slots or []

    # Use context-aware extractor with conversation slot state
    goal_info = await extract_training_goal(
        latest_user_message=message,
        conversation_context=conversation_context,
        awaiting_slots=awaiting_slots,
        today=today,
    )

    # Map to slots format
    distance = goal_info.race_distance
    race_date_str = goal_info.race_date
    race_date = None
    if race_date_str:
        parsed = parse_date_string(race_date_str)
        if parsed:
            # Convert datetime to date for slot storage
            race_date = parsed.date()

    slots: dict[str, str | date | None] = {
        "race_distance": distance,
        "race_date": race_date,
        "target_time": goal_info.target_finish_time,
    }

    logger.debug(
        "Extracted race slots with conversation context",
        slots=slots,
        intent="race_plan",
        conversation_id=conversation_id,
        has_slot_state=slot_state is not None,
    )

    return slots


def build_conversation_context_from_slot_state(
    slot_state: dict[str, str | date | int | float | bool | None],
) -> dict[str, str | None]:
    """Build conversation context dictionary from slot state.

    Converts slot state to format expected by extract_training_goal.
    Matches the format of build_conversation_context from plan_race.py.

    Args:
        slot_state: Current conversation slot state

    Returns:
        Conversation context dictionary with known facts
    """
    context: dict[str, str | None] = {
        "known_race_name": None,
        "known_race_distance": None,
        "known_race_date": None,
        "known_race_month": None,
        "known_target_time": None,
        "known_goal_type": None,
    }

    # Extract known race name
    race_name_value = slot_state.get("race_name")
    if isinstance(race_name_value, str) and race_name_value:
        context["known_race_name"] = race_name_value

    # Extract known race distance
    race_distance_value = slot_state.get("race_distance")
    if isinstance(race_distance_value, str) and race_distance_value:
        context["known_race_distance"] = race_distance_value

    # Extract known race date and month
    race_date_value = slot_state.get("race_date")
    if race_date_value:
        if isinstance(race_date_value, date):
            context["known_race_date"] = race_date_value.strftime("%Y-%m-%d")
            context["known_race_month"] = race_date_value.strftime("%B")
        elif isinstance(race_date_value, str):
            # Try to parse if it's a string
            parsed = parse_date_string(race_date_value)
            if parsed:
                context["known_race_date"] = parsed.strftime("%Y-%m-%d")
                context["known_race_month"] = parsed.strftime("%B")
            else:
                context["known_race_date"] = race_date_value

    # Extract known target time
    target_time_value = slot_state.get("target_time")
    if isinstance(target_time_value, str) and target_time_value:
        context["known_target_time"] = target_time_value

    # Extract known goal type
    goal_type_value = slot_state.get("goal_type")
    if isinstance(goal_type_value, str) and goal_type_value:
        context["known_goal_type"] = goal_type_value

    return context


async def extract_slots_for_intent(
    intent: str,
    horizon: str | None,
    message: str,
    _structured_data: dict,
    conversation_id: str | None = None,
    conversation_slot_state: dict[str, str | date | int | float | bool | None] | None = None,
) -> dict[str, str | date | int | float | bool | None]:
    """Extract slots for a given intent and horizon with conversation context.

    CRITICAL: Always passes conversation slot state to extraction functions so they can:
    - Avoid re-asking for already-filled slots
    - Infer missing context (e.g., year from previously known month)
    - Use conversation-level state for context-aware extraction

    Args:
        intent: Intent classification
        horizon: Planning horizon
        message: User message (MUST be from user, not system-generated)
        _structured_data: Structured data from orchestrator response (reserved for future use)
        conversation_id: Optional conversation ID for context-aware extraction
        conversation_slot_state: Current conversation slot state (single source of truth)

    Returns:
        Dictionary of extracted slots (values may be None)

    Note:
        NEVER extract slots from system-generated messages. This causes feedback loops.
        Only extract from actual user input.
    """
    slots: dict[str, str | date | int | float | bool | None] = {}

    # Detect system-generated messages (common patterns that indicate LLM output)
    # Do NOT extract slots from these - they cause feedback loops
    system_indicators = [
        "let's create",
        "let's build",
        "i'll create",
        "i'll build",
        "i'll generate",
        "let's plan",
        "i'm ready to",
        "i can build",
        "i can create",
    ]
    message_lower = message.lower().strip()
    is_system_message = any(indicator in message_lower for indicator in system_indicators)

    if is_system_message:
        logger.warning(
            "Skipping slot extraction from system-generated message",
            intent=intent,
            horizon=horizon,
            message_preview=message[:100],
            conversation_id=conversation_id,
        )
        return slots

    # Load conversation slot state if not provided (fallback)
    if conversation_slot_state is None and conversation_id:
        progress = get_conversation_progress(conversation_id)
        if progress and progress.slots:
            conversation_slot_state = progress.slots.copy()

    # Extract slots based on intent/horizon combination
    # Pass conversation_slot_state so extractors can use it for context-aware extraction
    if intent == "plan" and horizon == "race":
        race_slots = await extract_race_slots(
            message,
            conversation_id=conversation_id,
            conversation_slot_state=conversation_slot_state or {},
        )
        slots.update(race_slots)

    logger.debug(
        "Extracted slots for intent",
        intent=intent,
        horizon=horizon,
        slots=slots,
        conversation_id=conversation_id,
        has_conversation_context=conversation_slot_state is not None,
    )

    return slots
