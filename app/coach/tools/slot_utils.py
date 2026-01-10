"""Slot extraction and resolution utilities.

Provides functions for:
- Merging slots cumulatively
- Loose date parsing for conversational input
- Slot resolution logic
"""

from datetime import date, datetime, timezone
from typing import Any

from loguru import logger

from app.coach.utils.date_extraction import extract_date_from_text


def merge_slots(old_slots: dict[str, Any], new_slots: dict[str, Any]) -> dict[str, Any]:
    """Merge new slots into old slots cumulatively.

    CRITICAL RULE: Never overwrite a filled slot with None.

    Args:
        old_slots: Previous slot values
        new_slots: New slot values to merge

    Returns:
        Merged slots dictionary
    """
    merged = old_slots.copy()
    for key, value in new_slots.items():
        if value is not None:
            merged[key] = value
    return merged


def infer_year(month: int, day: int, today: date) -> int:
    """Infer year for a partial date (month + day only).

    Races are future-oriented - never infer a past date.

    Args:
        month: Month (1-12)
        day: Day (1-31)
        today: Today's date

    Returns:
        Inferred year
    """
    candidate = date(today.year, month, day)
    if candidate >= today:
        return today.year
    return today.year + 1


def parse_date_loose(
    text: str,
    today: date,
    known_slots: dict[str, Any] | None = None,
) -> date | None:
    """Parse date from conversational text using LLM extraction.

    Accepts partial date forms:
    - "25th" (day only)
    - "on the 25th!" (day with noise)
    - "April 25" (month + day)
    - "April 25th" (month + day with ordinal)
    - "4/25" (numeric month/day)
    - "April 25th 2026" (full date)

    If month is missing, looks at known_slots for context.

    Args:
        text: Text to parse
        today: Today's date for year inference
        known_slots: Previously extracted slots (may contain month info for context)

    Returns:
        Parsed date or None if parsing fails
    """
    # Build context from known_slots to help LLM
    context_parts = []
    if known_slots:
        for key, value in known_slots.items():
            if value is not None:
                if isinstance(value, str):
                    context_parts.append(f"{key}: {value}")
                elif isinstance(value, datetime):
                    context_parts.append(f"{key}: {value.strftime('%Y-%m-%d')}")
                    # Extract month if it's a date
                    month_name = value.strftime("%B")
                    context_parts.append(f"known_month: {month_name}")
                elif isinstance(value, date):
                    context_parts.append(f"{key}: {value.isoformat()}")
                    month_name = value.strftime("%B")
                    context_parts.append(f"known_month: {month_name}")

    context = ", ".join(context_parts) if context_parts else None
    if context:
        context = f"Context from previous conversation: {context}"

    # Use LLM extraction with context
    extracted_date = extract_date_from_text(
        text=text,
        context=context,
        min_date=today,  # Dates should be in the future
    )

    if extracted_date:
        logger.debug(
            "Parsed date using LLM extraction",
            date=extracted_date,
            text=text[:50],
            had_context=context is not None,
        )
        return extracted_date

    logger.debug("Could not parse date using LLM extraction", text=text[:50])
    return None
