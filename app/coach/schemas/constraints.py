"""Training constraints schema.

B17: User Feedback → Structured Constraints
Converts subjective user feedback into explicit, bounded, machine-readable constraints.
"""

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ConstraintReasonCode(StrEnum):
    """Reason codes for constraint derivation (non-LLM, deterministic)."""

    HIGH_FATIGUE = "HIGH_FATIGUE"
    REPORTED_PAIN = "REPORTED_PAIN"
    POOR_SLEEP = "POOR_SLEEP"
    LOW_MOTIVATION = "LOW_MOTIVATION"
    RECOVERY_MISMATCH = "RECOVERY_MISMATCH"
    SYSTEMIC_SORENESS = "SYSTEMIC_SORENESS"
    LOCALIZED_SORENESS = "LOCALIZED_SORENESS"
    HIGH_STRESS = "HIGH_STRESS"


class TrainingConstraints(BaseModel):
    """Structured constraints derived from user feedback.

    These constraints are INPUTS to downstream tools (B18 load adjustment, B8 planning).
    They do NOT modify training directly.

    Design principles:
    - All knobs are explicit and bounded
    - No open-ended numbers
    - No percentages without caps
    - No plan references
    - No calendar mutation
    """

    # Core volume/intensity constraints
    volume_multiplier: float = Field(
        default=1.0,
        description="Volume multiplier (0.6-1.1). Applied to planned volume.",
        ge=0.6,
        le=1.1,
    )
    intensity_cap: Literal["easy", "moderate", "hard", "none"] = Field(
        default="none",
        description="Maximum allowed intensity level. 'none' means no cap.",
    )
    force_rest_days: int = Field(
        default=0,
        description="Number of forced rest days required in next period",
        ge=0,
        le=3,
    )
    disallow_intensity_days: set[Literal["hard", "moderate"]] = Field(
        default_factory=set,
        description="Intensity levels to disallow (e.g., {'hard'} means no hard days)",
    )
    long_session_cap_minutes: int | None = Field(
        default=None,
        description="Maximum duration for long sessions in minutes (null = no cap)",
        ge=0,
    )

    # Expiry and metadata
    expiry_date: date = Field(
        description="ISO date when constraints expire (max 7 days from creation)",
    )
    source: Literal["user_feedback"] = Field(
        default="user_feedback",
        description="Source of constraints",
    )
    confidence: float = Field(
        default=0.0,
        description="Confidence in constraint derivation (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    reason_codes: list[ConstraintReasonCode] = Field(
        default_factory=list,
        description="Reason codes explaining why constraints exist (1-2 max)",
    )
    explanation: str = Field(
        default="",
        description="Single factual sentence explaining constraints",
        max_length=200,
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When constraints were created",
    )

    @property
    def is_expired(self) -> bool:
        """Check if constraints have expired."""
        today = datetime.now(timezone.utc).date()
        return today > self.expiry_date
