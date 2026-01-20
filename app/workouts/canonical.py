"""Canonical workout schema for LLM output and validation.

This module defines the strict Pydantic models that the LLM must produce.
These models are used for validation before persistence.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class StepIntensity(StrEnum):
    """Intensity levels for workout steps."""

    EASY = "easy"
    TEMPO = "tempo"
    LT2 = "lt2"
    THRESHOLD = "threshold"
    VO2 = "vo2"
    FLOW = "flow"
    REST = "rest"
    RACE = "race"


class StepTargetType(StrEnum):
    """Target type for workout steps."""

    NONE = "none"
    PACE = "pace"
    HR = "hr"
    POWER = "power"
    RPE = "rpe"


class WorkoutStep(BaseModel):
    """Canonical workout step schema.

    This is the exact shape the LLM must produce.
    """

    order: int = Field(description="Step order (0-indexed or 1-indexed, must be sequential)")
    name: str = Field(description="Step name/description")

    duration_seconds: int | None = Field(default=None, description="Step duration in seconds")
    distance_meters: int | None = Field(default=None, description="Step distance in meters")

    intensity: StepIntensity = Field(description="Step intensity level")
    target_type: StepTargetType = Field(default=StepTargetType.NONE, description="Target metric type")

    repeat: int = Field(default=1, ge=1, description="Number of times to repeat this step")
    is_recovery: bool = Field(default=False, description="Whether this is a recovery step")


class StructuredWorkout(BaseModel):
    """Complete structured workout from LLM.

    This is the exact shape the LLM must produce.
    """

    sport: str = Field(description="Sport type (run, bike, swim, etc.)")
    total_distance_meters: int | None = Field(default=None, description="Total workout distance in meters")
    total_duration_seconds: int | None = Field(default=None, description="Total workout duration in seconds")
    steps: list[WorkoutStep] = Field(description="Ordered list of workout steps")
