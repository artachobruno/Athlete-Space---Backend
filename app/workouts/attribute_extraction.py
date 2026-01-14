"""Deterministic attribute extraction for workout notes.

This module provides cheap, deterministic signal detection BEFORE LLM processing.
No ML, no guessing - just regex and keyword matching to extract explicit signals.

Purpose:
- Extract obvious signals (distance, duration, intervals) from raw notes
- Help LLM by pre-processing clear information
- Provide context for structured workout generation
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ExtractedWorkoutSignals:
    """Extracted signals from workout notes (deterministic only).

    This contains only signals that can be extracted with high confidence
    using regex/keywords. No interpretation or guessing.
    """

    distance_m: float | None = None
    duration_s: int | None = None
    has_intervals: bool = False
    intensity_hint: str | None = None


def extract_workout_signals(notes: str | None) -> ExtractedWorkoutSignals:
    """Extract deterministic signals from workout notes.

    This is a cheap, deterministic extraction - no ML, no interpretation.
    Only extracts signals that are explicitly present in the text.

    Args:
        notes: Raw workout notes text

    Returns:
        ExtractedWorkoutSignals with detected signals

    Examples:
        >>> extract_workout_signals("10 miles total, 6 mi at marathon pace")
        ExtractedWorkoutSignals(distance_m=16093.4, has_intervals=True, intensity_hint='marathon')

        >>> extract_workout_signals("1 hour easy run")
        ExtractedWorkoutSignals(duration_s=3600, intensity_hint='easy')
    """
    if not notes or not notes.strip():
        return ExtractedWorkoutSignals()

    notes_lower = notes.lower()

    # Extract distance (miles, km, meters)
    distance_m = _extract_distance(notes_lower)

    # Extract duration (minutes, hours)
    duration_s = _extract_duration(notes_lower)

    # Detect intervals (keywords: "interval", "repeat", "x", "×")
    has_intervals = _detect_intervals(notes_lower)

    # Extract intensity hints (easy, tempo, threshold, marathon pace, etc.)
    intensity_hint = _extract_intensity_hint(notes_lower)

    return ExtractedWorkoutSignals(
        distance_m=distance_m,
        duration_s=duration_s,
        has_intervals=has_intervals,
        intensity_hint=intensity_hint,
    )


def _extract_distance(notes_lower: str) -> float | None:
    """Extract distance in meters from notes.

    Looks for patterns like:
    - "10 miles" / "10 mi"
    - "16 km" / "16km"
    - "5000m" / "5000 meters"
    """
    # Pattern: number + unit (miles/mi, km, meters/m)
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*(?:miles|mi)\b", 1609.34),  # miles to meters
        (r"(\d+(?:\.\d+)?)\s*(?:km|kilometers?)\b", 1000.0),  # km to meters
        (r"(\d+(?:\.\d+)?)\s*(?:meters?|m)\b", 1.0),  # meters
    ]

    for pattern, multiplier in patterns:
        match = re.search(pattern, notes_lower)
        if match:
            try:
                value = float(match.group(1))
                return value * multiplier
            except ValueError:
                continue

    return None


def _extract_duration(notes_lower: str) -> int | None:
    """Extract duration in seconds from notes.

    Looks for patterns like:
    - "30 minutes" / "30 min"
    - "1 hour" / "1 hr"
    - "45:00" (time format)
    """
    # Pattern: number + unit (hours/hrs/hr, minutes/mins/min)
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", 3600),  # hours to seconds
        (r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|min)\b", 60),  # minutes to seconds
    ]

    for pattern, multiplier in patterns:
        match = re.search(pattern, notes_lower)
        if match:
            try:
                value = float(match.group(1))
                return int(value * multiplier)
            except ValueError:
                continue

    # Try time format (HH:MM or MM:SS)
    time_match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", notes_lower)
    if time_match:
        try:
            if time_match.group(3):  # HH:MM:SS
                hours = int(time_match.group(1))
                minutes = int(time_match.group(2))
                seconds = int(time_match.group(3))
                return hours * 3600 + minutes * 60 + seconds
            else:  # MM:SS (assume minutes:seconds)
                minutes = int(time_match.group(1))
                seconds = int(time_match.group(2))
                return minutes * 60 + seconds
        except ValueError:
            pass

    return None


def _detect_intervals(notes_lower: str) -> bool:
    """Detect if notes mention intervals.

    Looks for keywords: interval, repeat, x, ×, sets
    """
    interval_keywords = [
        r"\bintervals?\b",
        r"\brepeats?\b",
        r"\b\d+\s*x\s*\d+",  # "5 x 400"
        r"\b\d+\s*×\s*\d+",  # "5 × 400"
        r"\bsets\b",
        r"\breps\b",
    ]

    for pattern in interval_keywords:
        if re.search(pattern, notes_lower):
            return True

    return False


def _extract_intensity_hint(notes_lower: str) -> str | None:
    """Extract intensity hints from notes.

    Looks for common intensity keywords.
    Returns the first match found (priority: specific > general).
    """
    # Ordered from most specific to general
    intensity_patterns = [
        ("marathon pace", "marathon_pace"),
        ("half marathon pace", "half_marathon_pace"),
        ("5k pace", "5k_pace"),
        ("10k pace", "10k_pace"),
        ("threshold", "threshold"),
        ("vo2max", "vo2max"),
        ("vo2 max", "vo2max"),
        ("tempo", "tempo"),
        ("easy", "easy"),
        ("recovery", "recovery"),
        ("hard", "hard"),
        ("moderate", "moderate"),
    ]

    for pattern, intensity in intensity_patterns:
        if pattern in notes_lower:
            return intensity

    return None
