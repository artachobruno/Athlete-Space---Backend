"""Coach routing helpers: plan existence check and plan vs modify for week/today."""

from datetime import date, datetime, timezone

from sqlalchemy import select

from app.db.models import PlannedSession
from app.db.session import get_session
from app.utils.calendar import week_end, week_start


def has_existing_plan(user_id: str, start: date, end: date) -> bool:
    """Return True if there are planned sessions in the window.

    Must be fast and side-effect free. Queries planned sessions only;
    ignores executed (completed) sessions. Returns boolean only (no counts).

    Args:
        user_id: User ID (planned_sessions are keyed by user_id)
        start: Window start (inclusive)
        end: Window end (inclusive)

    Returns:
        True if any planned session exists in [start, end], False otherwise
    """
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


def route_plan_week_today(
    user_id: str | None,
    horizon: str,
    today: date | None,
) -> str:
    """Route plan + week/today to 'plan' (create) or 'modify' (change existing).

    Uses has_existing_plan as the only signal. No extracted attributes,
    no intent inference.

    Args:
        user_id: User ID for plan existence check; if None, default to 'plan'
        horizon: 'week' or 'today'
        today: Current date; if None, default to 'plan'

    Returns:
        'plan' if no plan exists or check cannot run; 'modify' if plan exists
    """
    if not user_id or not today:
        return "plan"
    if horizon == "week":
        start = week_start(today)
        end = week_end(today)
    elif horizon == "today":
        start = today
        end = today
    else:
        return "plan"
    if has_existing_plan(user_id, start, end):
        return "modify"
    return "plan"
