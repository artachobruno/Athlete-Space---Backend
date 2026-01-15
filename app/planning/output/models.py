"""MaterializedSession & WeekPlan - Planner Output.

These represent the final, concrete output of the planner.
Time (minutes) is PRIMARY, distance (miles) is DERIVED.
"""

from dataclasses import dataclass
from typing import Literal

from app.planning.library.session_template import SessionType
from app.plans.types import WorkoutIntent

Day = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True)
class MaterializedSession:
    """Fully materialized session with locked numbers.

    This is where math appears - but only after budgets are known.
    Once created, duration is PRIMARY and validated.
    Distance is DERIVED from duration x pace.

    Attributes:
        day: Day of week
        intent: Workout intent (rest, easy, long, quality) - required and immutable
        session_template_id: ID of template this session is based on
        session_type: Type of session
        duration_minutes: PRIMARY - duration in minutes (FINAL, validated)
        distance_miles: DERIVED - distance in miles (computed from duration x pace)
        notes: Optional notes
    """

    day: Day
    intent: WorkoutIntent  # Required - describes purpose, not pace
    session_template_id: str
    session_type: SessionType

    duration_minutes: int  # PRIMARY - FINAL, validated number
    distance_miles: float  # DERIVED - computed from duration x pace

    notes: str | None = None


@dataclass(frozen=True)
class WeekPlan:
    """Complete week with all sessions materialized.

    Attributes:
        week_index: Zero-based week index in the plan
        sessions: List of materialized sessions for this week
        total_duration_min: PRIMARY - total duration in minutes
        total_distance_miles: DERIVED - total distance in miles
    """

    week_index: int
    sessions: list[MaterializedSession]

    total_duration_min: int  # PRIMARY
    total_distance_miles: float  # DERIVED
