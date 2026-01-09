"""Slot extraction and resolution utilities.

Provides functions for:
- Merging slots cumulatively
- Loose date parsing for conversational input
- Slot resolution logic
"""

import re
from datetime import date, datetime, timezone
from typing import Any

from loguru import logger


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
    """Parse date from conversational text with loose matching.

    Accepts partial date forms:
    - "25th" (day only)
    - "on the 25th!" (day with noise)
    - "April 25" (month + day)
    - "April 25th" (month + day with ordinal)
    - "4/25" (numeric month/day)
    - "April 25th 2026" (full date)

    If month is missing, looks at known_slots.

    Args:
        text: Text to parse
        today: Today's date for year inference
        known_slots: Previously extracted slots (may contain month info)

    Returns:
        Parsed date or None if parsing fails
    """
    text_lower = text.lower().strip()

    # Month name to number mapping
    month_map = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }

    # Try full date patterns first (with year)
    full_date_patterns = [
        # ISO format: YYYY-MM-DD
        (r"(\d{4})-(\d{2})-(\d{2})", lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
        # US format: MM/DD/YYYY
        (r"(\d{1,2})/(\d{1,2})/(\d{4})", lambda m: (int(m.group(3)), int(m.group(1)), int(m.group(2)))),
        # Month name + day + year: "April 25, 2026" or "April 25th, 2026"
        (
            r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
            lambda m: (int(m.group(3)), month_map.get(m.group(1).lower(), 0), int(m.group(2))),
        ),
    ]

    for pattern, parser in full_date_patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                year, month, day = parser(match)
                if month == 0:
                    continue
                parsed_date = date(year, month, day)
                if parsed_date >= today:
                    logger.debug("Parsed full date", date=parsed_date, pattern=pattern)
                    return parsed_date
            except (ValueError, KeyError, IndexError):
                continue

    # Try partial date patterns (month + day, no year)
    partial_date_patterns = [
        # Month name + day: "April 25" or "April 25th"
        (
            r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\.?\s+(\d{1,2})(?:st|nd|rd|th)?",
            lambda m: (month_map.get(m.group(1).lower(), 0), int(m.group(2))),
        ),
        # Numeric month/day: "4/25" or "04/25"
        (r"(\d{1,2})/(\d{1,2})", lambda m: (int(m.group(1)), int(m.group(2)))),
    ]

    month: int | None = None
    day: int | None = None

    for pattern, parser in partial_date_patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                parsed_month, parsed_day = parser(match)
                if parsed_month == 0:
                    continue
                month = parsed_month
                day = parsed_day
                break
            except (ValueError, KeyError, IndexError):
                continue

    # If month is missing, try to infer from known_slots
    if month is None and known_slots:
        # Check if we have a previously mentioned month
        # This is a simple heuristic - could be enhanced
        for value in known_slots.values():
            if isinstance(value, str):
                for month_name, month_num in month_map.items():
                    if month_name in value.lower():
                        month = month_num
                        break
                if month:
                    break

    # Try day-only patterns: "25th" or "on the 25th!"
    if month is None and day is None:
        day_only_patterns = [
            r"(\d{1,2})(?:st|nd|rd|th)",
            r"on\s+the\s+(\d{1,2})(?:st|nd|rd|th)?",
        ]
        for pattern in day_only_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    day = int(match.group(1))
                    # If we have a month from known_slots, use it
                    # Otherwise, we can't parse day-only without month
                    if month:
                        break
                except (ValueError, IndexError):
                    continue

    # If we have month and day, infer year
    if month and day:
        year = infer_year(month, day, today)
        try:
            parsed_date = date(year, month, day)
            if parsed_date >= today:
                logger.debug(
                    "Parsed partial date with inferred year",
                    date=parsed_date,
                    month=month,
                    day=day,
                    year=year,
                )
                return parsed_date
        except ValueError:
            # Invalid date (e.g., Feb 30)
            logger.debug("Invalid date combination", month=month, day=day)
            return None

    # If we only have day and month from known_slots, try to use it
    if day and month is None and known_slots:
        # This is a fallback - we have day but no month
        # Could potentially use current month or next month
        # For now, return None
        logger.debug("Have day but no month", day=day)
        return None

    logger.debug("Could not parse date", text=text[:50])
    return None
