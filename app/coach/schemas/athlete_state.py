"""Input models for the Coach Agent.

These models represent the structured athlete state that the backend
passes to the Coach Agent for interpretation and guidance.

This is an agent schema - it defines the input contract for agents.
"""

from typing import Literal

from pydantic import BaseModel


class AthleteState(BaseModel):
    """Immutable snapshot of athlete training state.

    This is the input contract for the Coach Agent.
    All metrics and flags are pre-computed by the backend.
    """

    # Core load metrics
    ctl: float  # Chronic Training Load (42-day EWMA)
    atl: float  # Acute Training Load (7-day EWMA)
    tsb: float  # Training Stress Balance (CTL - ATL)

    # Trends
    load_trend: Literal["rising", "stable", "falling"]
    volatility: Literal["low", "medium", "high"]

    # Context
    days_since_rest: int
    days_to_race: int | None = None

    # Aggregates
    seven_day_volume_hours: float
    fourteen_day_volume_hours: float

    # Flags produced by rules engine
    flags: list[str]

    # Confidence in upstream calculations (0-1)
    confidence: float
