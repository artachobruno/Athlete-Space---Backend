from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class TrainingState(BaseModel):
    """Canonical, explainable snapshot of an athlete's training state.
    This is the ONLY object agents are allowed to reason over.
    """

    # --- Time context ---
    date: date

    # --- Load ---
    acute_load_7d: float = Field(..., ge=0)
    chronic_load_28d: float = Field(..., ge=0)
    training_stress_balance: float

    # --- Trends ---
    load_trend_7d: Literal["rising", "stable", "falling"]
    monotony: float = Field(..., ge=0)

    # --- Intensity ---
    intensity_distribution: dict[str, float]

    # --- Recovery & Risk ---
    recovery_status: Literal["under", "adequate", "over"]
    readiness_score: int = Field(..., ge=0, le=100)

    risk_flags: list[
        Literal[
            "OVERREACHING",
            "HIGH_MONOTONY",
            "ACUTE_SPIKE",
            "INSUFFICIENT_RECOVERY",
        ]
    ] = Field(default_factory=list)

    # --- Engine decision (authoritative) ---
    recommended_intent: Literal["RECOVER", "MAINTAIN", "BUILD"]
