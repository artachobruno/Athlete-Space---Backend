"""Pydantic schemas for LLM interaction.

This module defines Pydantic models used for:
- Validating LLM output (macro plans, session descriptions)
- Structuring LLM input (session generation context)

All schemas enforce strict validation to prevent LLM creativity
from introducing invalid values.
"""

from pydantic import BaseModel, Field

from app.domains.training_plan.enums import RaceDistance, TrainingIntent, WeekFocus


class MacroWeekSchema(BaseModel):
    """Schema for a single macro week in LLM output."""

    week: int = Field(..., ge=1, description="Week number (1-based)")
    focus: WeekFocus = Field(..., description="Training focus for this week")
    total_distance: float = Field(..., gt=0, description="Total weekly distance (must be > 0)")


class MacroPlanSchema(BaseModel):
    """Schema for complete macro plan from LLM.

    The LLM must provide intent and race_distance (if applicable),
    ensuring these values are never invented by the model.
    """

    intent: TrainingIntent = Field(..., description="User's training intent")
    race_distance: RaceDistance | None = Field(None, description="Race distance (None for seasons)")
    weeks: list[MacroWeekSchema] = Field(..., min_length=1, description="List of macro weeks")


class SessionTextInputSchema(BaseModel):
    """Schema for LLM session description generation input.

    Ensures LLM receives all required context and cannot invent
    intent or race distance.
    """

    template_id: str = Field(..., description="Session template identifier")
    distance: float = Field(..., gt=0, description="Session distance (must be > 0)")
    race_distance: RaceDistance | None = Field(None, description="Race distance context (None for seasons)")
    intent: TrainingIntent = Field(..., description="User's training intent")


class SessionTextOutputSchema(BaseModel):
    """Schema for LLM session description output.

    This schema enforces the exact structure required for session text generation.
    All fields must be present and validated.
    """

    title: str = Field(..., min_length=1, description="Session title")
    description: str = Field(..., min_length=1, description="Human-readable session description")
    structure: dict = Field(
        ...,
        description="warmup, main sets, cooldown with distances or times",
    )
    computed: dict = Field(
        ...,
        description="hard_minutes, intensity minutes, total distance",
    )
