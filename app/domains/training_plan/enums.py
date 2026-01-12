"""Canonical enums for planning dimensions.

This module defines all enumerations used across the planning system.
All enums are string-based to ensure JSON serialization compatibility
and alignment with RAG structure keys.
"""

from enum import StrEnum


# -----------------------------
# User Intent / Season Goal
# -----------------------------
class TrainingIntent(StrEnum):
    """User's subjective training goal for the plan."""

    MAINTAIN = "maintain"
    BUILD = "build"
    EXPLORE = "explore"
    RECOVER = "recover"


# -----------------------------
# Race Distances
# -----------------------------
class RaceDistance(StrEnum):
    """Canonical race distance identifiers."""

    FIVE_K = "5k"
    TEN_K = "10k"
    TEN_MILE = "10_mile"
    HALF_MARATHON = "half_marathon"
    MARATHON = "marathon"
    ULTRA = "ultra"


# -----------------------------
# Plan Type
# -----------------------------
class PlanType(StrEnum):
    """Type of training plan."""

    RACE = "race"
    SEASON = "season"
    WEEK = "week"


# -----------------------------
# Week Focus (RAG-mapped)
# -----------------------------
class WeekFocus(StrEnum):
    """Training focus for a specific week (RAG-mapped)."""

    BASE = "base"
    BUILD = "build"
    SHARPENING = "sharpening"
    SPECIFIC = "specific"
    TAPER = "taper"
    RECOVERY = "recovery"
    EXPLORATION = "exploration"


# -----------------------------
# Day Type
# -----------------------------
class DayType(StrEnum):
    """Type of training day."""

    EASY = "easy"
    QUALITY = "quality"
    LONG = "long"
    RACE = "race"
    REST = "rest"
    CROSS = "cross"
