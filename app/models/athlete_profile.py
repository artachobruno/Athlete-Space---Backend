"""Pydantic schemas for athlete profile and bio.

This module defines the structured athlete profile model used for:
- Storing athlete profile data in JSONB columns
- Generating narrative bios from structured data
- Validating profile updates
"""

from enum import StrEnum

from pydantic import BaseModel, Field

# ============================================================================
# Enums
# ============================================================================


class SportType(StrEnum):
    """Primary sport type."""

    RUN = "run"
    BIKE = "bike"
    TRI = "tri"
    UNKNOWN = "unknown"


class ExperienceLevel(StrEnum):
    """Athlete experience level."""

    BEGINNER = "beginner"
    STRUCTURED = "structured"
    COMPETITIVE = "competitive"
    UNKNOWN = "unknown"


class GoalType(StrEnum):
    """Training goal type."""

    PERFORMANCE = "performance"
    COMPLETION = "completion"
    GENERAL = "general"
    UNKNOWN = "unknown"


class RecoveryPreference(StrEnum):
    """Recovery preference."""

    ACTIVE = "active"
    PASSIVE = "passive"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class TrainingPhase(StrEnum):
    """Current training phase."""

    BASE = "base"
    BUILD = "build"
    PEAK = "peak"
    TAPER = "taper"
    RACE = "race"
    RECOVERY = "recovery"
    OFF_SEASON = "off_season"
    UNKNOWN = "unknown"


class CoachingStyle(StrEnum):
    """Preferred coaching style."""

    STRICT = "strict"
    FLEXIBLE = "flexible"
    GUIDED = "guided"
    UNKNOWN = "unknown"


class PlanFlexibility(StrEnum):
    """Plan flexibility preference."""

    RIGID = "rigid"
    MODERATE = "moderate"
    FLEXIBLE = "flexible"
    UNKNOWN = "unknown"


class FeedbackFrequency(StrEnum):
    """Feedback frequency preference."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ON_DEMAND = "on_demand"
    UNKNOWN = "unknown"


# ============================================================================
# Profile Models
# ============================================================================


class IdentityProfile(BaseModel):
    """Identity information for the athlete."""

    first_name: str | None = Field(default=None, description="First name")
    last_name: str | None = Field(default=None, description="Last name")
    age: int | None = Field(default=None, description="Age in years", ge=1, le=120)
    gender: str | None = Field(default=None, description="Gender identity")
    location: str | None = Field(default=None, description="Location/city")
    timezone: str | None = Field(default=None, description="IANA timezone string")


class GoalProfile(BaseModel):
    """Training goals for the athlete."""

    primary_goal: str | None = Field(default=None, description="Primary training goal")
    goal_type: GoalType = Field(default=GoalType.UNKNOWN, description="Goal type")
    target_event: str | None = Field(default=None, description="Target event name")
    target_date: str | None = Field(default=None, description="Target event date")
    performance_targets: list[str] = Field(default_factory=list, description="Performance targets")
    completion_targets: list[str] = Field(default_factory=list, description="Completion targets")


class ConstraintProfile(BaseModel):
    """Constraints and limitations for the athlete."""

    availability_days_per_week: int | None = Field(default=None, description="Available training days per week", ge=1, le=7)
    availability_hours_per_week: float | None = Field(default=None, description="Available training hours per week", ge=0.0, le=40.0)
    injury_status: str | None = Field(default=None, description="Current injury status")
    injury_notes: str | None = Field(default=None, description="Injury notes")
    restrictions: list[str] = Field(default_factory=list, description="Training restrictions")
    equipment_limitations: list[str] = Field(default_factory=list, description="Equipment limitations")


class TrainingContextProfile(BaseModel):
    """Training context and background."""

    primary_sport: SportType = Field(default=SportType.UNKNOWN, description="Primary sport")
    experience_level: ExperienceLevel = Field(default=ExperienceLevel.UNKNOWN, description="Experience level")
    years_training: float | None = Field(default=None, description="Years of structured training", ge=0.0)
    current_phase: TrainingPhase = Field(default=TrainingPhase.UNKNOWN, description="Current training phase")
    recent_performance: str | None = Field(default=None, description="Recent performance highlights")
    training_history_summary: str | None = Field(default=None, description="Training history summary")


class PreferenceProfile(BaseModel):
    """Training preferences for the athlete."""

    recovery_preference: RecoveryPreference = Field(default=RecoveryPreference.UNKNOWN, description="Recovery preference")
    coaching_style: CoachingStyle = Field(default=CoachingStyle.UNKNOWN, description="Preferred coaching style")
    plan_flexibility: PlanFlexibility = Field(default=PlanFlexibility.UNKNOWN, description="Plan flexibility preference")
    feedback_frequency: FeedbackFrequency = Field(default=FeedbackFrequency.UNKNOWN, description="Feedback frequency preference")
    preferred_training_times: list[str] = Field(default_factory=list, description="Preferred training times")
    preferred_workout_types: list[str] = Field(default_factory=list, description="Preferred workout types")
    disliked_workout_types: list[str] = Field(default_factory=list, description="Disliked workout types")


class NarrativeBio(BaseModel):
    """Narrative bio for the athlete.

    Generated from structured profile data using LLM.
    """

    text: str = Field(description="Bio text (3-5 sentences)")
    confidence_score: float = Field(description="Confidence score (0.0-1.0)", ge=0.0, le=1.0)
    source: str = Field(description="Bio source (ai_generated, user_edited, manual)")
    depends_on_hash: str | None = Field(default=None, description="Hash of profile data this bio depends on")


class AthleteProfile(BaseModel):
    """Complete athlete profile.

    Root model containing all structured profile data and optional narrative bio.
    """

    identity: IdentityProfile = Field(default_factory=IdentityProfile, description="Identity information")
    goals: GoalProfile = Field(default_factory=GoalProfile, description="Training goals")
    constraints: ConstraintProfile = Field(default_factory=ConstraintProfile, description="Constraints and limitations")
    training_context: TrainingContextProfile = Field(default_factory=TrainingContextProfile, description="Training context")
    preferences: PreferenceProfile = Field(default_factory=PreferenceProfile, description="Training preferences")
    narrative_bio: NarrativeBio | None = Field(default=None, description="Narrative bio (optional but always present logically)")
