"""Modification types for season-level workout modifications.

This module defines structured modification schemas for MODIFY → season operations.
All modifications are explicit and validated - no free-text mutation.
"""

from typing import Literal

from pydantic import BaseModel

SeasonChangeType = Literal[
    "reduce_volume",
    "increase_volume",
    "shift_season",
    "extend_phase",
    "reduce_phase",
    "protect_race",
]


class SeasonModification(BaseModel):
    """Structured modification request for a season (multiple weeks).

    All modifications are explicit and type-safe. Intent distribution is preserved
    by default unless explicitly overridden.

    Attributes:
        change_type: Type of modification to apply
        start_week: Start week number (1-based)
        end_week: End week number (1-based, inclusive)
        phase: Optional phase filter (base, build, peak, taper)
        percent: Percentage change (for volume modifications, 0.2 = 20%)
        miles: Absolute change in miles (for volume modifications)
        weeks: Number of weeks to extend/reduce (for phase modifications)
        reason: Optional reason for modification
    """

    change_type: SeasonChangeType

    start_week: int
    end_week: int

    phase: str | None = None

    percent: float | None = None
    miles: float | None = None
    weeks: int | None = None

    reason: str | None = None
