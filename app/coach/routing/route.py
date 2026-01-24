"""Coach routing: plan existence checks and CREATE vs MODIFY.

Read-only helpers. No side effects. Use existing read helpers only.
"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import PlannedSession, SeasonPlan
from app.db.session import get_session
from app.state.api_helpers import get_user_id_from_athlete_id
from app.utils.calendar import week_end, week_start


def has_planned_sessions_for_week(athlete_id: int) -> bool:
    """Return True if there are planned sessions in the current week.

    Read-only. Uses PlannedSession (user_id from athlete_id).
    """
    user_id = get_user_id_from_athlete_id(athlete_id)
    if not user_id:
        return False
    today = datetime.now(tz=timezone.utc).date()
    start = week_start(today)
    end = week_end(today)
    start_dt = datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt = datetime.combine(end, datetime.max.time()).replace(tzinfo=timezone.utc)
    with get_session() as session:
        row = session.execute(
            select(PlannedSession.id).where(
                PlannedSession.user_id == user_id,
                PlannedSession.starts_at >= start_dt,
                PlannedSession.starts_at <= end_dt,
                PlannedSession.status == "planned",
            ).limit(1)
        ).first()
    return row is not None


def has_active_plan_version(athlete_id: int, horizon: str) -> bool:
    """Return True if there is an active plan version for season/race.

    Read-only. Uses SeasonPlan (user_id from athlete_id). Horizon must be
    'season' or 'race'.
    """
    if horizon not in {"season", "race"}:
        return False
    user_id = get_user_id_from_athlete_id(athlete_id)
    if not user_id:
        return False
    try:
        with get_session() as session:
            row = session.execute(
                select(SeasonPlan.id).where(
                    SeasonPlan.user_id == user_id,
                    SeasonPlan.is_active.is_(True),
                ).limit(1)
            ).first()
    except Exception:
        return False
    else:
        return row is not None


def has_existing_plan(athlete_id: int, horizon: str) -> bool:
    """Return True if an existing plan exists for the given horizon.

    Read-only. No side effects. Dispatches to existing helpers only.
    """
    if horizon == "week":
        return has_planned_sessions_for_week(athlete_id)
    if horizon in {"season", "race"}:
        return has_active_plan_version(athlete_id, horizon)
    return False
