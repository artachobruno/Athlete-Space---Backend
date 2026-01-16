"""Calendar reconciliation service.

Read-only service that fetches data from database and runs reconciliation.
No database mutations. Deterministic and idempotent.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.reconciliation import (
    CompletedActivityInput,
    PlannedSessionInput,
    ReconciliationConfig,
    ReconciliationResult,
    reconcile_sessions,
)
from app.db.models import Activity, PlannedSession
from app.db.session import get_session


def reconcile_calendar(
    user_id: str,
    athlete_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    config: ReconciliationConfig | None = None,
) -> list[ReconciliationResult]:
    """Reconcile planned sessions with completed activities for a user.

    Fetches planned sessions and activities from database, then runs
    reconciliation algorithm. Read-only operation.

    Args:
        user_id: User ID (Clerk)
        athlete_id: Athlete ID (Strava)
        start_date: Optional start date for date range (defaults to 90 days ago)
        end_date: Optional end date for date range (defaults to 90 days from now)
        config: Optional reconciliation configuration

    Returns:
        List of reconciliation results, one per planned session
    """
    if start_date is None:
        start_date = (datetime.now(timezone.utc) - timedelta(days=90)).date()

    if end_date is None:
        end_date = (datetime.now(timezone.utc) + timedelta(days=90)).date()

    logger.info(
        f"[RECONCILIATION] Starting reconciliation for user_id={user_id}, athlete_id={athlete_id}, date_range={start_date} to {end_date}"
    )

    with get_session() as session:
        # Fetch planned sessions
        planned_sessions = _fetch_planned_sessions(
            session=session,
            user_id=user_id,
            athlete_id=athlete_id,
            start_date=start_date,
            end_date=end_date,
        )

        # Fetch completed activities
        completed_activities = _fetch_completed_activities(
            session=session,
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
        )

        # Convert to input models
        planned_inputs = [_planned_session_to_input(p) for p in planned_sessions]
        activity_inputs = [_activity_to_input(a) for a in completed_activities]

        # Run reconciliation
        results = reconcile_sessions(
            planned_sessions=planned_inputs,
            completed_activities=activity_inputs,
            config=config,
        )

        logger.info(
            f"[RECONCILIATION] Completed reconciliation: {len(results)} sessions processed, "
            f"{sum(1 for r in results if r.status.value == 'completed')} completed, "
            f"{sum(1 for r in results if r.status.value == 'missed')} missed"
        )

        return results


def _fetch_planned_sessions(
    session: Session,
    user_id: str,
    _athlete_id: int,  # Unused: kept for API compatibility
    start_date: date,
    end_date: date,
) -> list[PlannedSession]:
    """Fetch planned sessions from database.

    Args:
        session: Database session
        user_id: User ID
        athlete_id: Athlete ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        List of PlannedSession records
    """
    start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    result = session.execute(
        select(PlannedSession)
        .where(
            PlannedSession.user_id == user_id,
            PlannedSession.starts_at >= start_datetime,
            PlannedSession.starts_at <= end_datetime,
        )
        .order_by(PlannedSession.starts_at)
    )

    return list(result.scalars().all())


def _fetch_completed_activities(
    session: Session,
    user_id: str,
    start_date: date,
    end_date: date,
) -> list[Activity]:
    """Fetch completed activities from database.

    Args:
        session: Database session
        user_id: User ID
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        List of Activity records
    """
    start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    result = session.execute(
        select(Activity)
        .where(
            Activity.user_id == user_id,
            Activity.starts_at >= start_datetime,
            Activity.starts_at <= end_datetime,
        )
        .order_by(Activity.starts_at)
    )

    return list(result.scalars().all())


def _planned_session_to_input(planned: PlannedSession) -> PlannedSessionInput:
    """Convert PlannedSession model to input model.

    Args:
        planned: PlannedSession database model

    Returns:
        PlannedSessionInput
    """
    return PlannedSessionInput(
        session_id=planned.id,
        date=planned.date.date() if isinstance(planned.date, datetime) else planned.date,
        type=planned.type,
        duration_minutes=planned.duration_minutes,
        distance_km=planned.distance_km,
        intensity=planned.intensity,
        status=planned.status,
    )


def _activity_to_input(activity: Activity) -> CompletedActivityInput:
    """Convert Activity model to input model.

    Args:
        activity: Activity database model

    Returns:
        CompletedActivityInput
    """
    return CompletedActivityInput(
        activity_id=activity.id,
        start_time=activity.start_time,
        type=activity.type,
        duration_seconds=activity.duration_seconds,
        distance_meters=activity.distance_meters,
        source=activity.source,
    )
