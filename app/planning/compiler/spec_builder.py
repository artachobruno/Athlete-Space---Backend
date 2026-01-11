"""PlanSpecBuilder - Anchor Resolution.

This module resolves all planning anchors so downstream logic never infers.
It converts raw planning inputs into a fully valid PlanSpec.
"""

from datetime import date

from app.planning.schemas.plan_spec import GoalType, PlanSpec, RaceType


def build_plan_spec(
    *,
    goal_type: GoalType,
    race_type: RaceType | None,
    start_date: date,
    end_date: date | None,
    assumed_pace_min_per_mile: float,
    recent_weekly_duration_min: int,
    days_per_week: int,
    preferred_long_run_day: str,
    source: str,
    plan_version: str,
) -> PlanSpec:
    """Resolve all anchors and build a fully valid PlanSpec.

    Resolves all anchors:
    - end_date must exist
    - weekly_duration_targets_min must be generated
    - assumed pace must be resolved

    Args:
        goal_type: Type of goal (race, finish, improve, maintain)
        race_type: Type of race (optional, None for custom/maintain goals)
        start_date: When training plan begins
        end_date: When training plan ends (or race date). Must not be None.
        assumed_pace_min_per_mile: Pace model - minutes per mile (required)
        recent_weekly_duration_min: Recent weekly duration in minutes (base for progression)
        days_per_week: Number of training days per week (4-7)
        preferred_long_run_day: Day of week for long run ("sat" or "sun")
        source: Source of this plan spec (user, derived, fallback)
        plan_version: Version identifier for this plan spec

    Returns:
        Fully valid PlanSpec with all anchors resolved

    Raises:
        ValueError: If end_date is None, date range is invalid, or pace is invalid
    """
    if end_date is None:
        raise ValueError("end_date must be resolved before planning")

    total_weeks = ((end_date - start_date).days + 1) // 7
    if total_weeks <= 0:
        raise ValueError("Invalid date range")

    # ---- Pace resolution (single authority) ----
    if assumed_pace_min_per_mile <= 0:
        raise ValueError("Invalid assumed pace")

    # ---- Weekly time targets ----
    base_weekly_min = recent_weekly_duration_min
    weekly_targets = [
        int(base_weekly_min * min(1.0 + 0.05 * w, 1.25)) for w in range(total_weeks)
    ]

    return PlanSpec(
        goal_type=goal_type,
        race_type=race_type,
        start_date=start_date,
        end_date=end_date,
        weekly_duration_targets_min=weekly_targets,
        assumed_pace_min_per_mile=assumed_pace_min_per_mile,
        days_per_week=days_per_week,
        preferred_long_run_day=preferred_long_run_day,  # type: ignore
        source=source,  # type: ignore
        plan_version=plan_version,
    )
