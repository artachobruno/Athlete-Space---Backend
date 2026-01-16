"""Modification types for workout week modifications.

This module defines structured modification schemas for MODIFY → week operations.
All modifications are explicit and validated - no free-text mutation.
"""

from typing import Literal

from pydantic import BaseModel


class WeekModification(BaseModel):
    """Structured modification request for a week range.

    All modifications are explicit and type-safe. Intent distribution is preserved
    by default unless explicitly overridden.

    Attributes:
        change_type: Type of modification to apply
        start_date: Start date of week range (YYYY-MM-DD)
        end_date: End date of week range (YYYY-MM-DD)
        reason: Optional reason for modification
        percent: Percentage change (for volume modifications, 0.2 = 20%)
        miles: Absolute change in miles (for volume modifications)
        shift_map: Mapping of old dates to new dates (for shift_days)
        target_date: Target date for replace_day operation
        day_modification: DayModification dict for replace_day operation
    """

    change_type: Literal[
        "reduce_volume",
        "increase_volume",
        "shift_days",
        "replace_day",
    ]

    start_date: str  # YYYY-MM-DD
    end_date: str  # YYYY-MM-DD

    reason: str | None = None

    # Volume modifications (exactly one required for reduce/increase_volume)
    percent: float | None = None  # e.g., 0.2 for 20%
    miles: float | None = None  # absolute delta

    # Shift days
    shift_map: dict[str, str] | None = None  # {"2026-01-15": "2026-01-16"}

    # Delegate to day modification
    target_date: str | None = None  # YYYY-MM-DD
    day_modification: dict | None = None
