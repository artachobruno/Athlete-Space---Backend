"""Slot extraction utilities for extracting structured data from user messages.

This module provides functions to extract slots (structured data) from user messages
BEFORE tool routing, enabling precondition validation.
"""

from datetime import date

from loguru import logger

from app.coach.tools.plan_race import extract_race_information, parse_date_string


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


def extract_race_slots(message: str) -> dict[str, str | date | None]:
    """Extract race-related slots from user message.

    Args:
        message: User message containing race details

    Returns:
        Dictionary with slots:
        - race_distance: str | None
        - race_date: date | None
        - target_time: str | None (optional)
    """
    race_info = extract_race_information(message)
    distance = race_info.distance
    race_date_str = race_info.date
    race_date = None
    if race_date_str:
        parsed = parse_date_string(race_date_str)
        if parsed:
            # Convert datetime to date for slot storage
            race_date = parsed.date()

    slots: dict[str, str | date | None] = {
        "race_distance": distance,
        "race_date": race_date,
        "target_time": race_info.target_time,
    }

    logger.debug(
        "Extracted race slots",
        slots=slots,
        intent="race_plan",
    )

    return slots


def extract_slots_for_intent(
    intent: str, horizon: str | None, message: str, _structured_data: dict
) -> dict[str, str | date | int | float | bool | None]:
    """Extract slots for a given intent and horizon.

    Args:
        intent: Intent classification
        horizon: Planning horizon
        message: User message
        _structured_data: Structured data from orchestrator response (reserved for future use)

    Returns:
        Dictionary of extracted slots (values may be None)
    """
    slots: dict[str, str | date | int | float | bool | None] = {}

    # Extract slots based on intent/horizon combination
    if intent == "plan" and horizon == "race":
        race_slots = extract_race_slots(message)
        slots.update(race_slots)

    logger.debug(
        "Extracted slots for intent",
        intent=intent,
        horizon=horizon,
        slots=slots,
    )

    return slots
