"""Detector for upload requests in chat messages.

Detects if a user message contains activity or plan upload data.
"""

from __future__ import annotations

import re

from loguru import logger

UPLOAD_ACTIVITY_KEYWORDS = [
    "upload activity",
    "upload activities",
    "log activity",
    "log activities",
    "add activity",
    "add activities",
    "record activity",
    "record activities",
]

UPLOAD_PLAN_KEYWORDS = [
    "upload plan",
    "upload training plan",
    "upload schedule",
    "upload week",
    "upload sessions",
    "add plan",
    "add training plan",
]


def is_activity_upload(message: str) -> bool:
    """Detect if message is an activity upload request.

    Args:
        message: User message

    Returns:
        True if message appears to be an activity upload
    """
    message_lower = message.lower().strip()

    # Check for explicit upload keywords
    for keyword in UPLOAD_ACTIVITY_KEYWORDS:
        if keyword in message_lower:
            return True

    # Check if message contains CSV-like structure (comma-separated with headers)
    if "\n" in message and "," in message:
        lines = message.split("\n")
        if len(lines) >= 2:
            first_line = lines[0].lower()
            # Common CSV headers for activities
            activity_headers = ["date", "distance", "duration", "sport", "type", "time"]
            header_count = sum(1 for header in activity_headers if header in first_line)
            if header_count >= 2:
                logger.debug("Detected CSV activity upload by headers")
                return True

    # Check for activity-like free text patterns
    # Pattern: distance + duration (e.g., "ran 10 miles in 1:05")
    distance_pattern = r"\d+\s*(?:miles?|mi|kilometers?|km)"
    duration_pattern = r"\d{1,2}:\d{2}(?::\d{2})?"
    if re.search(distance_pattern, message_lower) and re.search(duration_pattern, message_lower):
        # Check for activity verbs
        activity_verbs = ["ran", "ran", "rode", "cycled", "swam", "walked", "completed", "did"]
        if any(verb in message_lower for verb in activity_verbs):
            logger.debug("Detected activity upload by free text pattern")
            return True

    return False


def is_plan_upload(message: str) -> bool:
    """Detect if message is a training plan upload request.

    Args:
        message: User message

    Returns:
        True if message appears to be a plan upload
    """
    message_lower = message.lower().strip()

    # Check for explicit upload keywords
    for keyword in UPLOAD_PLAN_KEYWORDS:
        if keyword in message_lower:
            return True

    # Check if message contains CSV-like structure for plans
    if "\n" in message and "," in message:
        lines = message.split("\n")
        if len(lines) >= 2:
            first_line = lines[0].lower()
            # Common CSV headers for plans
            plan_headers = ["date", "workout", "title", "session", "type", "intensity", "week"]
            header_count = sum(1 for header in plan_headers if header in first_line)
            if header_count >= 2:
                logger.debug("Detected CSV plan upload by headers")
                return True

    # Check for plan-like free text patterns
    # Pattern: day abbreviations + workout descriptions (e.g., "Mon easy 8, Tue intervals")
    day_pattern = r"\b(?:mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b"
    if re.search(day_pattern, message_lower):
        # Check for multiple days or workout descriptions
        day_matches = len(re.findall(day_pattern, message_lower))
        if day_matches >= 2:
            logger.debug("Detected plan upload by day pattern")
            return True

    return False
