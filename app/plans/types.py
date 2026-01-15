"""Canonical workout metrics schema.

This module defines the unit-safe, pace-aware planning foundation where:
- All distance calculations use MILES
- Volume is derived, never stored
- Every workout has a primary metric
- Pace prescription is explicit and explainable
- Race goal pace is the anchor
- Training paces are estimated, not guessed
"""

from typing import Literal, Optional

from pydantic import BaseModel

# Canonical workout intent set - DO NOT EXPAND YET
# Intent describes purpose, not pace. Intent is stable under modification.
WorkoutIntent = Literal["rest", "easy", "long", "quality"]


class PaceMetrics(BaseModel):
    """Pace metrics with explicit numeric backing.

    Attributes:
        pace_min_per_mile: Pace in minutes per mile (canonical, required if pace is present)
        pace_source: Source of the pace estimate
        zone: Training zone label (derived from pace, not free-text)
    """

    pace_min_per_mile: float | None = None  # canonical
    pace_source: Literal["race_goal", "training_estimate", "hr_estimate"]
    zone: Literal[
        "recovery", "easy", "z1", "z2", "lt1", "lt2", "tempo", "steady",
        "mp", "hmp", "10k", "5k", "vo2max", "threshold"
    ] | None = None


class WorkoutMetrics(BaseModel):
    """Workout metrics with unit-safe semantics.

    NOTE: Intent is session-level, not metrics-level.
    Intent belongs to MaterializedSession, not WorkoutMetrics.
    This allows MODIFY to replace metrics while preserving intent.

    Attributes:
        primary: Primary metric type (distance or duration)
        distance_miles: Distance in miles (ALWAYS miles, never km)
        duration_min: Duration in minutes
        pace: Optional pace metrics (must have numeric value if present)
        estimated_tss: Optional derived TSS estimate
    """

    primary: Literal["distance", "duration"]

    # Distance is ALWAYS miles
    distance_miles: float | None = None
    duration_min: int | None = None

    pace: PaceMetrics | None = None

    # Optional derived
    estimated_tss: int | None = None
