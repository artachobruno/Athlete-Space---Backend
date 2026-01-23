"""Detect contradictions in plan structure.

Tier 2 - Decision tool (non-mutating).
Detects incoherence between verdict/today plan/week plan/phase intent.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel

from app.tools.read.plans import get_planned_activities


class ConflictItem(BaseModel):
    """A single conflict detected."""

    conflict_type: str
    description: str
    severity: Literal["warning", "conflict"]
    suggested_resolution: str | None = None


class PlanIncoherenceResult(BaseModel):
    """Result from detect_plan_incoherence tool."""

    status: Literal["ok", "warning", "conflict"]
    conflict_items: list[ConflictItem]
    suggested_resolution: str | None = None


async def detect_plan_incoherence(  # noqa: RUF029
    user_id: str,
    athlete_id: int,
    horizon: Literal["today", "week", "season"],
    today: date | None = None,
) -> PlanIncoherenceResult:
    """Detect plan incoherence.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        horizon: Time horizon to check
        today: Current date (defaults to today)

    Returns:
        PlanIncoherenceResult with detected conflicts
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    logger.info(
        "Detecting plan incoherence",
        user_id=user_id,
        athlete_id=athlete_id,
        horizon=horizon,
    )

    # Calculate date range based on horizon
    if horizon == "today":
        start_date = today
        end_date = today
    elif horizon == "week":
        start_date = today
        end_date = today + timedelta(days=7)
    else:  # season
        start_date = today
        end_date = today + timedelta(days=90)

    # Get current plan state (sync function)
    planned_sessions = get_planned_activities(
        user_id=user_id,
        start=start_date,
        end=end_date,
    )

    conflict_items: list[ConflictItem] = []

    # Check for basic incoherence (v1 - simplified)
    # Check for overlapping sessions on same day
    sessions_by_date: dict[date, list] = {}
    for session in planned_sessions:
        if session.date not in sessions_by_date:
            sessions_by_date[session.date] = []
        sessions_by_date[session.date].append(session)

    for session_date, sessions in sessions_by_date.items():
        if len(sessions) > 1:
            # Multiple sessions on same day - check if intentional
            sports = {s.sport for s in sessions}
            if len(sports) == 1:
                conflict_items.append(
                    ConflictItem(
                        conflict_type="duplicate_sessions",
                        description=f"Multiple {sports.pop()} sessions on {session_date}",
                        severity="warning",
                        suggested_resolution="Review if multiple sessions on same day are intentional",
                    )
                )

    # Check for missing rest days (v1 - basic check)
    if horizon in {"week", "season"}:
        # Count consecutive training days
        sorted_dates = sorted(sessions_by_date.keys())
        consecutive_days = 0
        max_consecutive = 0
        for i, session_date in enumerate(sorted_dates):
            if i > 0 and (session_date - sorted_dates[i - 1]).days == 1:
                consecutive_days += 1
            else:
                consecutive_days = 1
            max_consecutive = max(max_consecutive, consecutive_days)

        if max_consecutive > 7:
            conflict_items.append(
                ConflictItem(
                    conflict_type="insufficient_rest",
                    description=f"{max_consecutive} consecutive training days without rest",
                    severity="warning",
                    suggested_resolution="Add rest days to prevent overtraining",
                )
            )

    # Determine overall status
    has_conflicts = any(item.severity == "conflict" for item in conflict_items)
    has_warnings = any(item.severity == "warning" for item in conflict_items)

    if has_conflicts:
        status: Literal["ok", "warning", "conflict"] = "conflict"
    elif has_warnings:
        status = "warning"
    else:
        status = "ok"

    # Suggested resolution
    suggested_resolution: str | None = None
    if conflict_items:
        resolutions = [item.suggested_resolution for item in conflict_items if item.suggested_resolution]
        if resolutions:
            suggested_resolution = "; ".join(resolutions[:3])

    return PlanIncoherenceResult(
        status=status,
        conflict_items=conflict_items,
        suggested_resolution=suggested_resolution,
    )
