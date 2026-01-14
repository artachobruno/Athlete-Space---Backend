"""Pydantic request/response models for onboarding."""

from typing import Literal

from pydantic import BaseModel, Field


class OnboardingCompleteRequest(BaseModel):
    """Request for onboarding completion.

    This is a single atomic payload that collects all required athlete profile data.
    No partial updates - all required fields must be provided.
    """

    # Identity (ALWAYS REQUIRED)
    role: Literal["athlete", "coach"] = Field(description="User role: athlete or coach")
    first_name: str = Field(description="User's first name")
    last_name: str | None = Field(default=None, description="User's last name (optional)")
    timezone: str = Field(description="IANA timezone string (e.g., 'America/New_York')")

    # Training Context (REQUIRED)
    primary_sport: Literal["run", "bike", "tri"] = Field(description="Primary sport")
    goal_type: Literal["performance", "completion", "general"] = Field(description="Training goal type")
    experience_level: Literal["beginner", "structured", "competitive"] = Field(description="Experience level")
    availability_days_per_week: int = Field(description="Available training days per week", ge=1, le=7)
    availability_hours_per_week: float = Field(description="Available training hours per week", ge=1.0, le=40.0)

    # Health (REQUIRED BUT LIGHT)
    injury_status: Literal["none", "managing", "injured"] = Field(description="Current injury status")
    injury_notes: str | None = Field(default=None, description="Optional injury notes", max_length=500)

    # Optional: Plan generation
    generate_initial_plan: bool = Field(
        default=False,
        description="Whether to generate initial training plan (opt-in)",
    )


class OnboardingCompleteResponse(BaseModel):
    """Response for onboarding completion."""

    status: str = Field(description="Status: 'ok'")
    weekly_intent: dict | None = Field(default=None, description="Generated weekly intent if available")
    season_plan: dict | None = Field(default=None, description="Generated season plan if available")
    provisional: bool = Field(default=False, description="Whether plans are provisional")
    warning: str | None = Field(default=None, description="Warning message if plan generation failed")
