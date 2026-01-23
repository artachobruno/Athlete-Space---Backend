"""Season narrative view schemas.

These schemas define the structure for the Season tab - a read-only,
story-driven view that explains how the season is unfolding relative to the plan.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class GoalRace(BaseModel):
    """Goal race information for the season."""

    name: str = Field(..., description="Race name")
    date: date = Field(..., description="Race date")
    weeks_to_race: int = Field(..., description="Weeks remaining until race")


class SeasonWeek(BaseModel):
    """A week in the season narrative."""

    week_index: int = Field(..., description="Week number in the season (1-based)")
    date_range: str = Field(..., description="Date range for the week (e.g., 'Dec 29 – Jan 4')")
    status: Literal["completed", "current", "upcoming"] = Field(
        ..., description="Week status"
    )
    coach_summary: str = Field(
        ...,
        description="LLM-generated coach summary (1-2 sentences) explaining how the week unfolded relative to plan intent",
    )
    key_sessions: list[str] = Field(
        default_factory=list,
        description="List of key session names for this week (names only, no metrics)",
    )
    flags: list[Literal["fatigue", "missed_sessions"]] = Field(
        default_factory=list,
        description="Optional flags indicating risks or issues",
    )


class SeasonPhase(BaseModel):
    """A training phase in the season."""

    name: str = Field(
        ...,
        description="Phase name (e.g., 'Base', 'Build', 'Peak', 'Taper')",
    )
    intent: str = Field(
        ...,
        description="Human-readable phase intent explaining the purpose of this phase",
    )
    weeks: list[SeasonWeek] = Field(
        ...,
        description="List of weeks in this phase",
    )


class SeasonSummary(BaseModel):
    """Complete season narrative summary."""

    goal_race: GoalRace | None = Field(
        default=None,
        description="Goal race information if available",
    )
    current_phase: str = Field(
        ...,
        description="Name of the current training phase",
    )
    phases: list[SeasonPhase] = Field(
        ...,
        description="List of training phases in chronological order",
    )
