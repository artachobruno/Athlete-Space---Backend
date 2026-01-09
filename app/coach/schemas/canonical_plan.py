"""Canonical plan model.

This is the single, unified plan format that replaces all planning tools.
Supports day, week, and season horizons.
"""

from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel, Field


class PlanSession(BaseModel):
    """A single session within a plan."""

    date: date_type = Field(description="Session date (YYYY-MM-DD)")
    type: str = Field(description="Activity type: Run, Ride, Rest, etc.")
    intensity: Literal["easy", "moderate", "hard", "race"] | None = Field(default=None, description="Session intensity")
    duration_minutes: int | None = Field(default=None, description="Duration in minutes")
    notes: str | None = Field(default=None, description="Additional notes/instructions")


class CanonicalPlan(BaseModel):
    """Canonical plan object.

    This is the only mutable coaching artifact.
    All planning tools produce this format.
    """

    horizon: Literal["day", "week", "season"] = Field(description="Time horizon of the plan")
    start_date: date_type = Field(description="Plan start date (YYYY-MM-DD)")
    end_date: date_type = Field(description="Plan end date (YYYY-MM-DD)")
    sessions: list[PlanSession] = Field(default_factory=list, description="List of planned sessions")
    assumptions: list[str] = Field(default_factory=list, description="Assumptions made in planning")
    constraints: list[str] = Field(default_factory=list, description="Constraints considered")
