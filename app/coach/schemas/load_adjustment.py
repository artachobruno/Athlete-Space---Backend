"""B18: Training Load Adjustment Decision schema.

Output contract for load adjustment tool.
This is NOT a plan and NOT a mutation - it's a decision that planning (B8) will respect.
"""

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class AdjustmentReasonCode(StrEnum):
    """Reason codes for load adjustments (non-LLM, deterministic)."""

    HIGH_FATIGUE = "HIGH_FATIGUE"
    ATL_SPIKE = "ATL_SPIKE"
    TSB_LOW = "TSB_LOW"
    HIGH_VARIANCE = "HIGH_VARIANCE"
    POOR_RECOVERY = "POOR_RECOVERY"
    CONSTRAINT_DRIVEN = "CONSTRAINT_DRIVEN"
    BACK_TO_BACK_HARD = "BACK_TO_BACK_HARD"


class LoadAdjustmentDecision(BaseModel):
    """Load adjustment decision output.

    This decision is consumed by planning (B8) to adjust load parameters.
    It does NOT modify plans directly.
    """

    volume_delta_pct: float = Field(
        description="Volume adjustment as percentage change (-0.40 to +0.10)",
        ge=-0.40,
        le=0.10,
    )
    intensity_cap: Literal["easy", "moderate", "none"] = Field(
        default="none",
        description="Maximum allowed intensity level. 'none' means no cap.",
    )
    long_session_cap_minutes: int | None = Field(
        default=None,
        description="Maximum duration for long sessions in minutes (null = no cap)",
        ge=45,
    )
    forced_rest_days: list[str] = Field(
        default_factory=list,
        description="ISO date strings (YYYY-MM-DD) for forced rest days",
    )
    effective_window_days: int = Field(
        description="Number of days this adjustment is effective (1-7)",
        ge=1,
        le=7,
    )
    reason_codes: list[AdjustmentReasonCode] = Field(
        default_factory=list,
        description="Reason codes explaining the adjustment (1-3 max)",
    )
    confidence: float = Field(
        description="Confidence in this adjustment (0.0-1.0)",
        ge=0.0,
        le=1.0,
    )
    explanation: str = Field(
        description="Single factual sentence explaining the adjustment",
        max_length=200,
    )
    applied_constraints: list[str] = Field(
        default_factory=list,
        description="List of constraint types that were applied (e.g., 'volume_multiplier', 'intensity_cap')",
    )
