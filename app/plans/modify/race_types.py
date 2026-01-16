"""Modification types for race modifications.

This module defines structured modification schemas for MODIFY → race operations.
Race modifications only update metadata - they never mutate sessions directly.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

RaceChangeType = Literal[
    "change_date",
    "change_distance",
    "change_priority",
    "change_taper",
]


class RaceModification(BaseModel):
    """Structured modification request for race attributes.

    Race modifications only update race metadata:
    - race_date
    - race_distance
    - race_priority
    - taper_weeks

    They NEVER:
    - Edit sessions directly
    - Auto-shift workouts
    - Auto-add race-pace workouts
    - Auto-extend season

    Attributes:
        change_type: Type of modification to apply
        new_race_date: New race date (YYYY-MM-DD) if changing date
        new_distance_km: New race distance in km if changing distance
        new_priority: New race priority (A/B/C) if changing priority
        new_taper_weeks: New taper length in weeks if changing taper
        reason: Optional reason for modification
        allow_plan_inconsistency: Allow modifications that may cause plan inconsistencies
    """

    change_type: RaceChangeType

    new_race_date: date | None = None
    new_distance_km: float | None = None
    new_priority: str | None = None
    new_taper_weeks: int | None = None

    reason: str | None = None
    allow_plan_inconsistency: bool = False
