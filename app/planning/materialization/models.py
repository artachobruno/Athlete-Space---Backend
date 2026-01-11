"""Concrete Session Output Schema.

Defines the final immutable representation of a workout.
All sessions produced by Phase 5 must match this schema.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class IntervalBlock:
    """An interval block within a session.

    Attributes:
        reps: Number of repetitions
        work_min: Work duration in minutes per rep
        rest_min: Rest duration in minutes per rep
        intensity: Intensity description (e.g., "threshold", "5k pace")
    """

    reps: int
    work_min: float
    rest_min: float
    intensity: str


@dataclass(frozen=True)
class ConcreteSession:
    """Fully concrete training session.

    This is the final output of Phase 5 materialization.
    All numeric values are locked and validated.

    Attributes:
        day: Day of week
        session_template_id: ID of template this session is based on
        session_type: Type of session
        duration_minutes: PRIMARY - duration in minutes (locked)
        distance_miles: DERIVED - distance in miles (computed from duration x pace)
        warmup_minutes: Optional warmup duration
        cooldown_minutes: Optional cooldown duration
        intervals: Optional list of interval blocks
        instructions: Optional coach text (LLM-generated, text only)
    """

    day: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    session_template_id: str
    session_type: str

    duration_minutes: int  # PRIMARY
    distance_miles: float  # DERIVED

    warmup_minutes: int | None = None
    cooldown_minutes: int | None = None

    intervals: list["IntervalBlock"] | None = None

    instructions: str | None = None  # LLM allowed (text only)
