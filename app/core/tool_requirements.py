"""Tool requirements schema - single source of truth for required slots per tool.

This module defines which slots are required for each tool before execution.
This is checked BEFORE tool execution to prevent errors.
"""

from datetime import date
from typing import Literal

# Type aliases for slot types
SlotType = str | date | int | float | bool | None

# Required slots per tool
# Format: tool_name -> {slot_name: slot_type}
TOOL_REQUIRED_SLOTS: dict[str, dict[str, type[SlotType]]] = {
    "plan_race_build": {
        "race_distance": str,
        "race_date": date,
    },
}


def get_required_slots(tool_name: str) -> dict[str, type[SlotType]]:
    """Get required slots for a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Dictionary mapping slot names to their types, empty dict if tool not found
    """
    return TOOL_REQUIRED_SLOTS.get(tool_name, {})


def has_required_slots(tool_name: str, slots: dict[str, SlotType]) -> tuple[bool, list[str]]:
    """Check if all required slots are present for a tool.

    Args:
        tool_name: Name of the tool
        slots: Dictionary of extracted slots (values may be None)

    Returns:
        Tuple of (can_execute, missing_slots)
        - can_execute: True if all required slots are present and non-None
        - missing_slots: List of missing slot names
    """
    required = get_required_slots(tool_name)
    if not required:
        # Tool has no requirements, can execute
        return True, []

    missing = []
    for slot_name in required:
        slot_value = slots.get(slot_name)
        if slot_value is None:
            missing.append(slot_name)

    return len(missing) == 0, missing
