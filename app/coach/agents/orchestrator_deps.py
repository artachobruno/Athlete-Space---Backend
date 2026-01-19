"""Dependencies for the Coach Orchestrator Agent.

Provides context and dependencies needed by the pydantic_ai agent.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.coach.execution_guard import TurnExecutionGuard
from app.coach.schemas.athlete_state import AthleteState


class AthleteProfileData(BaseModel):
    """Athlete profile data for agent context."""

    gender: str | None = None
    age: int | None = None
    weight_lbs: float | None = None
    height_in: float | None = None
    unit_system: str | None = None


class TrainingPreferencesData(BaseModel):
    """Training preferences data for agent context."""

    training_consistency: str | None = None
    years_structured: int | None = None
    primary_sports: list[str] | None = None
    available_days: list[str] | None = None
    weekly_training_hours: float | None = None
    primary_training_goal: str | None = None
    training_focus: str | None = None
    injury_flag: bool | None = None


class RaceProfileData(BaseModel):
    """Race profile data extracted from goals."""

    event_name: str | None = None
    event_type: str | None = None
    event_date: str | None = None
    target_time: str | None = None
    distance: str | None = None
    location: str | None = None
    raw_text: str | None = None


class StructuredProfileData(BaseModel):
    """Structured profile data for agent context (read-only)."""

    constraints: dict | None = None
    structured_profile: dict | None = None
    narrative_bio: str | None = None
    profile_last_updated_at: str | None = None


class CoachDeps(BaseModel):
    """Dependencies for the Coach Orchestrator Agent.

    Provides context that tools and the agent can access.

    CRITICAL: This is read-only context. The Coach agent MUST NEVER mutate profile data.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    athlete_id: int
    user_id: str | None = None  # User ID (Clerk) - used for storing planned sessions
    athlete_state: AthleteState | None = None
    athlete_profile: AthleteProfileData | None = None
    training_preferences: TrainingPreferencesData | None = None
    race_profile: RaceProfileData | None = None
    structured_profile_data: StructuredProfileData | None = None  # Structured profile (read-only)
    days: int = 60
    days_to_race: int | None = None
    execution_guard: TurnExecutionGuard | None = Field(
        default=None, exclude=True
    )  # Turn-scoped execution guard (excluded from serialization)
