"""Clarification message generator for missing slots.

Generates proactive clarification messages that drive conversation toward execution
by explicitly requesting missing required slots.

Design Principle:
    NO_ACTION does not mean passive. It means "drive clarification without side effects."

CORE INVARIANT:
    If an executable action exists and is blocked only by missing slots,
    the system MUST ask for those slots and MUST NOT chat.
"""

from loguru import logger

from app.core.slot_extraction import extract_slots_for_intent
from app.core.slot_gate import REQUIRED_SLOTS


def generate_execution_confirmation(action: str) -> str:
    """Generate execution confirmation request (NO LLM).

    State 3: Slots complete, execution allowed, but waiting for permission.
    This is a state transition instruction, not conversational.

    Args:
        action: Tool name that would execute if confirmed

    Returns:
        Deterministic confirmation message asking for explicit execution permission
    """
    if action == "plan_race_build":
        return "I'm ready to build your marathon training plan.\n\nDo you want me to create it now?"

    if action == "plan_week":
        return "I'm ready to create your weekly training plan.\n\nDo you want me to build it now?"

    return "I'm ready to proceed. Should I go ahead?"


def generate_slot_clarification(action: str, missing_slots: list[str]) -> str:
    """Generate deterministic slot clarification (NO LLM).

    SINGLE-QUESTION RULE: Returns exactly ONE question. No paragraphs. No lists.

    This is a state transition instruction, not conversational.

    Args:
        action: Tool name that would execute if slots were present
        missing_slots: List of missing slot names

    Returns:
        Single question asking for the next missing slot
    """
    # Plan race flow - ask for ONE slot at a time
    if action == "plan_race_build":
        # Priority: race_date first, then race_distance
        if "race_date" in missing_slots:
            return "What is the date of your marathon?"

        if "race_distance" in missing_slots:
            return "What distance are you training for?"

        # Multiple slots missing - ask for first one
        if missing_slots:
            return f"What is the {missing_slots[0].replace('_', ' ')}?"

    # Weekly planning flow - requires race plan (gate handled in executor)
    if action == "plan_week":
        return "I can plan your week once your marathon plan is created. What is your marathon date?"

    # Fallback - single question only
    if missing_slots:
        slot_name = missing_slots[0].replace("_", " ")
        return f"What is your {slot_name}?"

    return "What information do you need?"


def generate_proactive_clarification(
    tool_name: str,
    missing_slots: list[str],
    next_action: str | None = None,
) -> str:
    """Generate clarification message that proactively requests missing slots.

    Args:
        tool_name: Name of the tool that requires the slots
        missing_slots: List of missing slot names
        next_action: Optional description of what action will happen once slots are provided

    Returns:
        User-friendly clarification message that explicitly requests missing slots
    """
    if not missing_slots:
        return "I can help with that. What information would you like?"

    # Map slot names to user-friendly descriptions
    slot_descriptions = {
        "race_date": "race date (e.g., April 25, 2026 or 4/25)",
        "race_distance": "race distance (e.g., 5K, 10K, half marathon, marathon, ultra)",
        "target_time": "target finish time (optional)",
    }

    # Ask for ONE slot at a time, ordered by importance
    primary_slot = missing_slots[0]
    primary_description = slot_descriptions.get(primary_slot, primary_slot.replace("_", " "))

    # Determine what action will happen once slots are provided
    if next_action:
        action_text = next_action
    elif tool_name == "plan_race_build":
        action_text = "build your race training plan"
    elif tool_name == "plan_week":
        action_text = "create your weekly training plan"
    else:
        action_text = "proceed"

    # Special handling for weekly plans requiring race info
    if tool_name == "plan_week" and ("race_date" in missing_slots or "race_distance" in missing_slots):
        message = "I can create your weekly plan once I know your race date and distance. What race are you training for?"
    # Generate focused clarification asking for primary slot
    elif len(missing_slots) == 1:
        message = f'I can {action_text}, but I need the **{primary_description}**.\n\nExample: "April 25th" or "on the 25th!"'
    else:
        # Multiple slots missing - ask for the primary one first
        message = f'I can {action_text}. To get started, I need the **{primary_description}**.\n\nExample: "April 25th" or "on the 25th!"'

    logger.info(
        "Generated clarification for missing slots",
        tool_name=tool_name,
        missing_slots=missing_slots,
        primary_slot=primary_slot,
    )

    return message


def determine_missing_slots_for_intent(
    intent: str,
    horizon: str | None,
    _message: str,
    _conversation_id: str | None = None,
) -> tuple[str | None, list[str]]:
    """Determine which tool would be called and what slots are missing.

    This is used BEFORE orchestrator execution to provide context about missing slots.

    Args:
        intent: User intent (e.g., "plan")
        horizon: Planning horizon (e.g., "race", "week")
        _message: User message (unused, reserved for future use)
        _conversation_id: Optional conversation ID for context (unused, reserved for future use)

    Returns:
        Tuple of (tool_name, missing_slots)
        - tool_name: Name of tool that would be executed, or None if intent doesn't map to a tool
        - missing_slots: List of missing slot names (empty if all slots present or no tool)
    """
    # Map intent/horizon to tool name
    intent_to_tool: dict[tuple[str, str], str] = {
        ("plan", "race"): "plan_race_build",
        ("plan", "week"): "plan_week",
        ("plan", "season"): "plan_season",
    }

    # Only look up if horizon is not None (keys require tuple[str, str])
    tool_key: tuple[str, str] | None = None
    if horizon is not None:
        tool_key = (intent, horizon)
    tool_name = intent_to_tool.get(tool_key) if tool_key is not None else None

    if not tool_name:
        # Intent doesn't map to a tool with slot requirements
        return None, []

    # Check if tool has required slots
    required = REQUIRED_SLOTS.get(tool_name, [])
    if not required:
        # Tool has no slot requirements
        return tool_name, []

    # Note: This is async, but we'll need to handle this differently
    # For now, return tool name and required slots - the executor will do proper extraction
    return tool_name, required
