"""Explain plan structure and rationale.

Tier 1 - Informational tool (non-mutating).
Explains why plan has its current structure, not metrics.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel

from app.tools.read.plans import get_planned_activities


class PlanStructureExplanation(BaseModel):
    """Explanation of plan structure."""

    phase_intent: str
    week_template_rationale: str
    key_workouts_rationale: list[str]
    overall_structure: str


async def explain_plan_structure(  # noqa: RUF029
    user_id: str,
    athlete_id: int,
    horizon: Literal["week", "season", "race"],
    today: date | None = None,
) -> PlanStructureExplanation:
    """Explain plan structure and rationale.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        horizon: Time horizon to explain
        today: Current date (defaults to today)

    Returns:
        PlanStructureExplanation with rationale
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    logger.info(
        "Explaining plan structure",
        user_id=user_id,
        athlete_id=athlete_id,
        horizon=horizon,
    )

    # Calculate date range based on horizon
    if horizon == "week":
        start_date = today
        end_date = today + timedelta(days=7)
    elif horizon == "season":
        start_date = today
        end_date = today + timedelta(days=90)
    else:  # race
        start_date = today
        end_date = today + timedelta(days=180)

    # Get current plan state (sync function)
    planned_sessions = get_planned_activities(
        user_id=user_id,
        start=start_date,
        end=end_date,
    )

    # Analyze structure (v1 - simplified)
    # Group by week
    sessions_by_week: dict[int, list] = {}
    for session in planned_sessions:
        week_num = (session.date - today).days // 7
        if week_num not in sessions_by_week:
            sessions_by_week[week_num] = []
        sessions_by_week[week_num].append(session)

    # Extract key workouts
    key_workouts: list[str] = []
    for week_sessions in list(sessions_by_week.values())[:4]:  # First 4 weeks
        key_workouts.extend(
            f"{session.sport} {session.intensity} on {session.date}"
            for session in week_sessions
            if session.intensity in {"high", "moderate"}
        )

    # Build explanation (v1 - template-based)
    phase_intent = f"Plan structure for {horizon} focuses on progressive overload and periodization"

    week_template_rationale = (
        f"Weekly structure includes {len(sessions_by_week.get(0, []))} sessions "
        f"with variation in intensity and volume"
    )

    key_workouts_rationale = key_workouts[:5] if key_workouts else ["No key workouts identified"]

    overall_structure = (
        f"The {horizon} plan is structured with {len(planned_sessions)} total sessions "
        f"across {len(sessions_by_week)} weeks, emphasizing progressive adaptation"
    )

    return PlanStructureExplanation(
        phase_intent=phase_intent,
        week_template_rationale=week_template_rationale,
        key_workouts_rationale=key_workouts_rationale,
        overall_structure=overall_structure,
    )
