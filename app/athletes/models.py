"""Athlete pace profile models.

Defines athlete-specific pace anchors and preferences for planning.
"""

from typing import Literal, Optional

from pydantic import BaseModel


class AthletePaceProfile(BaseModel):
    """Athlete pace profile - race-first anchor for all pace calculations.

    Attributes:
        race_goal_pace_min_per_mile: Race goal pace in minutes per mile (primary anchor)
        hr_zones: Optional heart rate zones in bpm ranges (secondary estimate source)
    """

    race_goal_pace_min_per_mile: float
    hr_zones: dict[str, dict[str, int]] | None = None  # e.g., {"z1": {"min": 120, "max": 140}}


class AthletePlanningPreferences(BaseModel):
    """Athlete preferences for planning.

    Attributes:
        preferred_volume_unit: Whether athlete prefers distance or duration-based planning
        pace_profile: Pace profile with race goal pace anchor
    """

    preferred_volume_unit: Literal["distance", "duration"]
    pace_profile: AthletePaceProfile
