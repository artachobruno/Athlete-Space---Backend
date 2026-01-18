"""Message validation for Style LLM output."""

import re


def validate_message(text: str) -> None:
    """Validate generated message against hard constraints.

    Args:
        text: Generated message text

    Raises:
        ValueError: If message breaks rules

    This validator enforces:
    - Maximum 4 sentences
    - Maximum one metric (detected via number of dashes/numbers)
    - Maximum 6 numeric characters (prevents dashboard creep)
    - Forbidden wording patterns
    """
    sentences = [s for s in text.split(".") if s.strip()]

    if len(sentences) > 4:
        raise ValueError("Too many sentences")

    # Check for too many numeric characters (prevents dashboard/metric dump)
    numeric_count = len(re.findall(r"\d", text))
    if numeric_count > 6:
        raise ValueError("Too many numeric characters")

    # Check for metric dumps (too many numbers with dashes)
    # This is a heuristic: if there are multiple numbers and dashes, likely a metric dump
    if any(char.isdigit() for char in text) and text.count("-") > 1:
        raise ValueError("Too many metrics")

    # Check for forbidden wording
    forbidden = ["should", "must", "I changed", "I updated"]
    for word in forbidden:
        if word in text.lower():
            raise ValueError(f"Forbidden wording: {word}")
