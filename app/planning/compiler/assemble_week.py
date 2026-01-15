"""Assemble Minimal WeekPlan.

This module produces a real WeekPlan without templates yet.
Distance is always derived from time x pace.
"""

from app.planning.compiler.week_skeleton import Day, WeekSkeleton
from app.planning.library.session_template import SessionType
from app.planning.output.models import MaterializedSession, WeekPlan
from app.plans.types import WorkoutIntent


def assemble_week_plan(
    *,
    week_index: int,
    allocation: dict[Day, int],
    skeleton: WeekSkeleton,
    pace_min_per_mile: float,
) -> WeekPlan:
    """Assemble a WeekPlan from skeleton and allocation.

    Produces a real WeekPlan with:
    - All sessions materialized
    - Distance derived from time x pace
    - session_template_id set to "UNASSIGNED"

    Args:
        week_index: Zero-based week index in the plan
        allocation: Dictionary mapping days to allocated minutes
        skeleton: Week structure definition
        pace_min_per_mile: Pace model - minutes per mile

    Returns:
        WeekPlan with all sessions materialized
    """
    sessions = []
    total_minutes = 0
    total_miles = 0.0

    # Map DayRole to SessionType
    role_to_session_type: dict[str, SessionType] = {
        "long": "long",
        "hard": "tempo",  # Default hard session type
        "easy": "easy",
        "rest": "rest",
    }

    # Map DayRole to WorkoutIntent
    # Intent describes purpose, not pace. Intent is stable under modification.
    role_to_intent: dict[str, WorkoutIntent] = {
        "long": "long",
        "hard": "quality",  # Hard days are quality sessions
        "easy": "easy",
        "rest": "rest",
    }

    for day, minutes in allocation.items():
        # Only create sessions for days with non-zero minutes
        if minutes == 0:
            continue

        role = skeleton.days[day]
        session_type = role_to_session_type[role]
        intent = role_to_intent[role]

        # Distance is DERIVED from time x pace
        miles = round(minutes / pace_min_per_mile, 2)

        sessions.append(
            MaterializedSession(
                day=day,
                intent=intent,
                session_template_id="UNASSIGNED",
                session_type=session_type,
                duration_minutes=minutes,
                distance_miles=miles,
            )
        )

        total_minutes += minutes
        total_miles += miles

    return WeekPlan(
        week_index=week_index,
        sessions=sessions,
        total_duration_min=total_minutes,
        total_distance_miles=round(total_miles, 2),
    )
