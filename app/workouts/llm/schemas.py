"""Strict output schemas for LLM workout interpretations.

These schemas enforce type safety and validation for all LLM outputs.
Any output failing schema validation is discarded.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StepLLMInterpretation(BaseModel):
    """LLM interpretation for a single workout step.

    Strict schema for step-level coaching feedback.
    """

    rating: Literal["excellent", "good", "ok", "needs_work"] = Field(
        description="Execution rating: excellent, good, ok, or needs_work"
    )
    summary: str = Field(description="Brief explanation of the rating")
    coaching_tip: str = Field(description="One actionable coaching tip")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0 and 1")


class WorkoutLLMInterpretation(BaseModel):
    """LLM interpretation for a complete workout.

    Strict schema for workout-level coaching feedback.
    """

    verdict: Literal["successful", "partially_successful", "missed"] = Field(
        description="Overall workout verdict: successful, partially_successful, or missed"
    )
    summary: str = Field(description="Brief explanation of the verdict")
