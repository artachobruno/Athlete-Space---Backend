"""Modification types for workout day modifications.

This module defines structured modification schemas for MODIFY → day operations.
All modifications are explicit and validated - no free-text mutation.
"""

from typing import Literal, Optional

from pydantic import BaseModel

from app.plans.types import WorkoutIntent


class DayModification(BaseModel):
    """Structured modification request for a single workout day.

    All modifications are explicit and type-safe. Intent is preserved
    by default unless explicitly overridden.

    Attributes:
        change_type: Type of modification to apply
        value: Modification value (type depends on change_type)
        reason: Optional reason for modification
        explicit_intent_change: Optional intent override (None = preserve intent)
    """

    change_type: Literal[
        "adjust_distance",
        "adjust_duration",
        "adjust_pace",
        "replace_metrics",
    ]

    value: float | str | dict | None = None
    reason: str | None = None

    # Optional, explicit override only
    # If None → intent MUST remain unchanged
    explicit_intent_change: WorkoutIntent | None = None
