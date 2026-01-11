"""Slot gate - central, mandatory slot validation before tool execution.

This module provides a central gate that validates all required slots
are present BEFORE any tool is called. This prevents tools from executing
with incomplete information.
"""

from datetime import date
from typing import Literal

from loguru import logger

# Type aliases for slot types
SlotType = str | date | int | float | bool | None


class SlotValidationError(Exception):
    """Raised when required slots are missing.

    This is a developer error - orchestration logic should never
    allow execution to proceed if required slots are missing.
    """

    def __init__(self, tool_name: str, missing_slots: list[str], message: str | None = None):
        self.tool_name = tool_name
        self.missing_slots = missing_slots
        self.message = message or f"Missing required slots for {tool_name}: {', '.join(missing_slots)}"
        super().__init__(self.message)


# Required slots per tool - single source of truth
REQUIRED_SLOTS: dict[str, list[str]] = {
    "plan_race_build": ["race_date", "race_distance"],
    # Add other tools here as needed
}


def validate_slots(tool_name: str, slots: dict[str, SlotType]) -> tuple[bool, list[str]]:
    """Validate that all required slots are present for a tool.

    This is a MANDATORY gate - tools must NOT execute if slots are missing.

    Args:
        tool_name: Name of the tool to validate
        slots: Dictionary of extracted slots (values may be None)

    Returns:
        Tuple of (can_execute, missing_slots)
        - can_execute: True if all required slots are present and non-None
        - missing_slots: List of missing slot names (empty if can_execute=True)
    """
    required = REQUIRED_SLOTS.get(tool_name, [])
    if not required:
        # Tool has no requirements, can execute
        logger.debug(f"Tool {tool_name} has no slot requirements", tool=tool_name)
        return True, []

    missing: list[str] = []
    for slot_name in required:
        slot_value = slots.get(slot_name)
        if slot_value is None:
            missing.append(slot_name)

    can_execute = len(missing) == 0

    if not can_execute:
        logger.info(
            "Slot validation failed - missing required slots",
            tool=tool_name,
            missing_slots=missing,
            available_slots=list(slots.keys()),
        )
    else:
        logger.debug(
            "Slot validation passed",
            tool=tool_name,
            required_slots=required,
        )

    return can_execute, missing


def validate_slots_strict(tool_name: str, slots: dict[str, SlotType], required_attributes: list[str] | None = None) -> None:
    """Validate that all required slots are present for a tool (strict version that raises).

    This is a MANDATORY gate - tools must NOT execute if slots are missing.
    This version raises an exception instead of returning a tuple.

    Args:
        tool_name: Name of the tool to validate
        slots: Dictionary of extracted slots (values may be None)
        required_attributes: Optional list of required attributes (if not provided, uses REQUIRED_SLOTS)

    Raises:
        SlotValidationError: If required slots are missing
    """
    if required_attributes is not None:
        required = required_attributes
    else:
        required = REQUIRED_SLOTS.get(tool_name, [])

    if not required:
        # Tool has no requirements, can execute
        logger.debug(f"Tool {tool_name} has no slot requirements", tool=tool_name)
        return

    missing: list[str] = []
    for slot_name in required:
        slot_value = slots.get(slot_name)
        if slot_value is None:
            missing.append(slot_name)

    if missing:
        logger.error(
            "Slot validation failed - missing required slots (strict)",
            tool=tool_name,
            missing_slots=missing,
            available_slots=list(slots.keys()),
        )
        raise SlotValidationError(tool_name, missing)

    logger.debug(
        "Slot validation passed (strict)",
        tool=tool_name,
        required_slots=required,
    )
