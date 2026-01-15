"""Week planner distribution logic - miles-only.

This module provides week planning utilities that enforce:
- Distance-first distribution (miles only)
- Duration only if explicitly required
- Race goal pace as anchor for all pace calculations
"""

from typing import Literal

from app.athletes.models import AthletePaceProfile
from app.plans.pace import estimate_pace
from app.plans.types import PaceMetrics, WorkoutIntent, WorkoutMetrics


def get_target_weekly_volume_miles(
    athlete_pace_profile: AthletePaceProfile | None,
    weekly_volume_hours: float | None = None,
    weekly_volume_miles: float | None = None,
) -> float:
    """Get target weekly volume in miles.

    Prioritizes explicit miles target. If not provided, converts hours to miles
    using race goal pace as anchor.

    Args:
        athlete_pace_profile: Athlete pace profile with race goal pace
        weekly_volume_hours: Optional weekly volume in hours
        weekly_volume_miles: Optional weekly volume in miles (takes precedence)

    Returns:
        Target weekly volume in miles

    Raises:
        ValueError: If no volume source provided or pace profile missing when needed
    """
    if weekly_volume_miles is not None:
        return weekly_volume_miles

    if weekly_volume_hours is None:
        raise ValueError("Either weekly_volume_miles or weekly_volume_hours must be provided")

    if athlete_pace_profile is None:
        # Fallback: assume 8 min/mile pace
        return weekly_volume_hours * 7.5
    # Convert hours to miles using race goal pace
    # At race pace: miles = hours * (60 / pace_min_per_mile)
    pace_min_per_mile = athlete_pace_profile.race_goal_pace_min_per_mile
    miles_per_hour = 60.0 / pace_min_per_mile
    return weekly_volume_hours * miles_per_hour


def create_workout_metrics(
    primary: str,
    distance_miles: float | None = None,
    duration_min: int | None = None,
    zone: str | None = None,
    race_goal_pace: float | None = None,
) -> WorkoutMetrics:
    """Create WorkoutMetrics with optional pace.

    NOTE: Intent is session-level, not metrics-level.
    Intent should be set on MaterializedSession, not WorkoutMetrics.
    This allows MODIFY to replace metrics while preserving intent.

    Args:
        primary: Primary metric type ("distance" or "duration")
        distance_miles: Distance in miles (required if primary="distance")
        duration_min: Duration in minutes (required if primary="duration")
        zone: Training zone (optional, used to estimate pace)
        race_goal_pace: Race goal pace in min/mile (required if zone provided)

    Returns:
        WorkoutMetrics with optional pace

    Raises:
        ValueError: If required parameters missing
    """
    if primary == "distance":
        valid_primary: Literal["distance", "duration"] = "distance"
    elif primary == "duration":
        valid_primary = "duration"
    else:
        raise ValueError(f"Invalid primary: {primary}. Must be 'distance' or 'duration'")

    pace: PaceMetrics | None = None

    if zone and race_goal_pace:
        pace = estimate_pace(zone=zone, race_pace=race_goal_pace, pace_source="race_goal")

    return WorkoutMetrics(
        primary=valid_primary,
        distance_miles=distance_miles,
        duration_min=duration_min,
        pace=pace,
    )


def assign_intent_from_day_type(
    day_type: str,
    is_long_run_day: bool = False,
    is_quality_day: bool = False,
    is_rest_day: bool = False,
) -> WorkoutIntent:
    """Assign workout intent based on day type and flags.

    Rules:
    - Exactly 1 long per week (use is_long_run_day flag)
    - 0-2 quality depending on phase (use is_quality_day flag)
    - Rest days are "rest"
    - All others are "easy"

    Args:
        day_type: Day type from skeleton (long, hard, easy, rest)
        is_long_run_day: Whether this is the designated long run day
        is_quality_day: Whether this is a quality/hard day
        is_rest_day: Whether this is a rest day

    Returns:
        WorkoutIntent for this day
    """
    if is_rest_day or day_type == "rest":
        return "rest"
    if is_long_run_day or day_type == "long":
        return "long"
    if is_quality_day or day_type == "hard":
        return "quality"
    return "easy"


def infer_intent_from_session_type(session_type: str) -> WorkoutIntent:
    """Infer workout intent from session type string.

    ⚠️ PLANNING ONLY - DO NOT USE IN MODIFY LOGIC
    This function is for initial planning where intent is not yet set.
    MODIFY must preserve intent from original session, never re-infer.

    Used for single-day planning where session type is known.
    Maps common session types to canonical intents.

    Args:
        session_type: Session type string (e.g., "long", "tempo", "easy", "rest")

    Returns:
        WorkoutIntent inferred from session type
    """
    session_type_lower = session_type.lower()

    # Rest sessions
    if "rest" in session_type_lower:
        return "rest"

    # Long runs
    if "long" in session_type_lower:
        return "long"

    # Quality/hard sessions
    quality_keywords = [
        "tempo",
        "threshold",
        "vo2",
        "interval",
        "speed",
        "fartlek",
        "hill",
        "quality",
        "hard",
        "race",
    ]
    if any(keyword in session_type_lower for keyword in quality_keywords):
        return "quality"

    # Default to easy
    return "easy"
