"""Pydantic request/response models for onboarding."""

from pydantic import BaseModel, Field

from app.api.schemas.schemas import AthleteProfileUpdateRequest, TrainingPreferencesUpdateRequest


class OnboardingCompleteRequest(BaseModel):
    """Request for onboarding completion."""

    profile: AthleteProfileUpdateRequest | None = Field(default=None, description="Profile data to persist")
    training_preferences: TrainingPreferencesUpdateRequest | None = Field(
        default=None,
        description="Training preferences to persist",
    )
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
