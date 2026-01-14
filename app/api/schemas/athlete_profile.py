"""Shared schema for athlete profile upsert operations.

This schema is used by both onboarding and settings endpoints to ensure
a single source of truth for athlete profile data.
"""

from typing import Literal

from pydantic import BaseModel, Field


class AthleteProfileUpsert(BaseModel):
    """Shared schema for creating/updating athlete profile.

    Used by both onboarding completion and settings update endpoints.
    All fields are required to ensure data completeness.
    """

    # Identity (stored in users table)
    first_name: str = Field(description="User's first name")
    last_name: str | None = Field(default=None, description="User's last name (optional)")
    timezone: str = Field(description="IANA timezone string (e.g., 'America/New_York')")

    # Training Context (stored in athlete_profiles and user_settings)
    primary_sport: Literal["run", "bike", "tri"] = Field(description="Primary sport")
    goal_type: Literal["performance", "completion", "general"] = Field(description="Training goal type")
    experience_level: Literal["beginner", "structured", "competitive"] = Field(description="Experience level")
    availability_days_per_week: int = Field(description="Available training days per week", ge=1, le=7)
    availability_hours_per_week: float = Field(description="Available training hours per week", ge=1.0, le=40.0)

    injury_status: Literal["none", "managing", "injured"] = Field(description="Current injury status")
    injury_notes: str | None = Field(default=None, description="Optional injury notes", max_length=500)
