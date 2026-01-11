"""PlanSpec - Immutable Input Contract.

This is the ONLY object allowed to enter the planning compiler.
It represents a complete, validated planning request.

ARCHITECTURAL COMMITMENT: TIME-BASED PLANNING
=============================================
Time (minutes) is the only allocatable quantity.
Distance (miles) is always derived from: distance_miles = duration_minutes x pace_min_per_mile
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

RaceType = Literal["5k", "10k", "half", "marathon", "custom"]
GoalType = Literal["race", "finish", "improve", "maintain"]


@dataclass(frozen=True)
class PlanSpec:
    """Complete planning specification - immutable input contract.

    This is the only object allowed to enter the planning compiler.
    No dynamic computation allowed downstream - all values are snapshots.

    Attributes:
        goal_type: Type of goal (race, finish, improve, maintain)
        race_type: Type of race (optional, None for custom/maintain goals)
        start_date: When training plan begins
        end_date: When training plan ends (or race date)
        weekly_duration_targets_min: Duration in minutes per week for each week (PRIMARY currency)
        assumed_pace_min_per_mile: Pace model - minutes per mile (required, even if inferred)
        days_per_week: Number of training days per week (4-7)
        preferred_long_run_day: Day of week for long run ("sat" or "sun")
        source: Source of this plan spec (user, derived, fallback)
        plan_version: Version identifier for this plan spec
    """

    # ---- Required anchors ----
    goal_type: GoalType
    race_type: RaceType | None
    start_date: date
    end_date: date

    # ---- Time-based planning (PRIMARY) ----
    weekly_duration_targets_min: list[int]  # PRIMARY currency

    # ---- Pace model (required, even if inferred) ----
    assumed_pace_min_per_mile: float

    # ---- Constraints ----
    days_per_week: int  # 4-7
    preferred_long_run_day: Literal["sat", "sun"]

    # ---- Metadata ----
    source: Literal["user", "derived", "fallback"]
    plan_version: str
