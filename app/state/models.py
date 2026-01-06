"""Canonical state models for athlete training data.

These models represent the domain state - pure data structures
with no business logic. They are used for state computation
and agent input/output.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class ActivityRecord(BaseModel):
    """Domain model for a single training activity.

    This is the canonical representation of an activity,
    independent of source (Strava, Garmin, etc.).
    """

    athlete_id: int
    activity_id: str
    source: Literal["strava", "garmin"]
    sport: str
    start_time: dt.datetime
    duration_sec: int
    distance_m: float
    elevation_m: float
    avg_hr: int | None
    power: dict | None


class TrainingState(BaseModel):
    """Canonical, explainable snapshot of an athlete's training state.

    Pure state only. NO LOGIC.
    """

    # --- Time context ---
    date: dt.date

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

    # --- Engine decision ---
    recommended_intent: Literal["RECOVER", "MAINTAIN", "BUILD"]


class NutritionState(BaseModel):
    """Canonical snapshot of athlete nutrition state.

    Pure state only. NO LOGIC.
    """

    energy_balance: Literal["deficit", "neutral", "surplus"]
    carb_adequacy: Literal["low", "adequate", "high"]
    protein_adequacy: Literal["low", "adequate"]
    hydration_risk: bool
    supplement_flags: list[str]
