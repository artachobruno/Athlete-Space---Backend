"""Calendar API endpoints with real activity data.

Step 6: Replaces mock data with real activities from database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user_id
from app.api.schemas.schemas import (
    CalendarSeasonResponse,
    CalendarSession,
    CalendarSessionsResponse,
    CalendarTodayResponse,
    CalendarWeekResponse,
)
from app.calendar.reconciliation_service import reconcile_calendar
from app.db.models import Activity, PlannedSession, StravaAccount
from app.db.session import get_session

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _get_athlete_id(session: Session, user_id: str) -> int | None:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        session: Database session
        user_id: User ID

    Returns:
        Athlete ID as integer, or None if not found
    """
    account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
    if account:
        try:
            return int(account[0].athlete_id)
        except (ValueError, TypeError):
            return None
    return None


def _get_planned_sessions_safe(
    session: Session,
    user_id: str,
    start_date: datetime,
    end_date: datetime,
) -> list[PlannedSession]:
    """Get planned sessions with safe error handling.

    Args:
        session: Database session
        user_id: User ID
        start_date: Start date
        end_date: End date

    Returns:
        List of planned sessions, empty list on schema errors
    """
    try:
        planned_sessions = (
            session.execute(
                select(PlannedSession)
                .where(
                    PlannedSession.user_id == user_id,
                    PlannedSession.date >= start_date,
                    PlannedSession.date <= end_date,
                    # NULL-safe status filter: exclude only explicitly excluded statuses
                    # NULL statuses and "planned" statuses are included
                    func.coalesce(PlannedSession.status, "planned").notin_(["completed", "cancelled", "skipped"]),
                )
                .order_by(PlannedSession.date)
            )
            .scalars()
            .all()
        )
        return list(planned_sessions)
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.warning(f"[CALENDAR] Database schema issue querying planned sessions. Missing column. Returning empty: {e!r}")
            # Rollback the transaction to prevent "InFailedSqlTransaction" errors on subsequent queries
            session.rollback()
            return []
        raise


def _get_activities_safe(
    session: Session,
    user_id: str,
    start_date: datetime,
    end_date: datetime,
    matched_activity_ids: set[str],
) -> list[CalendarSession]:
    """Get activities with safe error handling.

    Args:
        session: Database session
        user_id: User ID
        start_date: Start date
        end_date: End date
        matched_activity_ids: Set of activity IDs already matched to planned sessions

    Returns:
        List of activity sessions, empty list on schema errors
    """
    try:
        activities = (
            session.execute(
                select(Activity)
                .where(
                    Activity.user_id == user_id,
                    Activity.start_time >= start_date,
                    Activity.start_time <= end_date,
                )
                .order_by(Activity.start_time)
            )
            .scalars()
            .all()
        )
        return [_activity_to_session(a) for a in activities if a.id not in matched_activity_ids]
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.warning(f"[CALENDAR] Database schema issue querying activities. Missing column. Returning empty: {e!r}")
            # Rollback the transaction to prevent "InFailedSqlTransaction" errors on subsequent queries
            session.rollback()
            return []
        raise


def _run_reconciliation_safe(
    user_id: str,
    athlete_id: int,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, str], set[str]]:
    """Run reconciliation with safe error handling.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        start_date: Start date
        end_date: End date

    Returns:
        Tuple of (reconciliation_map, matched_activity_ids)
    """
    reconciliation_map: dict[str, str] = {}
    matched_activity_ids: set[str] = set()

    try:
        reconciliation_results = reconcile_calendar(
            user_id=user_id,
            athlete_id=athlete_id,
            start_date=start_date,
            end_date=end_date,
        )
        for result in reconciliation_results:
            reconciliation_map[result.session_id] = result.status.value
            if result.matched_activity_id:
                matched_activity_ids.add(result.matched_activity_id)
    except Exception as e:
        logger.warning(f"[CALENDAR] Reconciliation failed, using planned status: {e!r}")

    return reconciliation_map, matched_activity_ids


def _planned_session_to_calendar(
    planned: PlannedSession,
    reconciliation_status: str | None = None,
) -> CalendarSession:
    """Convert PlannedSession to CalendarSession.

    Args:
        planned: PlannedSession record
        reconciliation_status: Optional status from reconciliation (overrides planned.status)

    Returns:
        CalendarSession object
    """
    time_str = planned.time if planned.time else None

    # Use reconciliation status if provided, otherwise use planned status
    status = reconciliation_status if reconciliation_status else planned.status

    return CalendarSession(
        id=planned.id,
        date=planned.date.strftime("%Y-%m-%d"),
        time=time_str,
        type=planned.type,
        title=planned.title,
        duration_minutes=planned.duration_minutes,
        distance_km=round(planned.distance_km, 2) if planned.distance_km else None,
        intensity=planned.intensity,
        status=status,
        notes=planned.notes,
    )


def _activity_to_session(activity: Activity) -> CalendarSession:
    """Convert Activity to CalendarSession.

    Args:
        activity: Activity record

    Returns:
        CalendarSession object
    """
    # Determine intensity based on duration
    if activity.duration_seconds is None:
        duration_hours = 0.0
        duration_minutes = 0
    else:
        duration_hours = activity.duration_seconds / 3600.0
        duration_minutes = int(activity.duration_seconds / 60)

    if duration_hours > 1.5:
        intensity = "easy"
    elif duration_hours > 0.75:
        intensity = "moderate"
    else:
        intensity = "hard"

    # Format time
    time_str = activity.start_time.strftime("%H:%M")

    # Determine distance in km
    if activity.distance_meters is not None and activity.distance_meters > 0:
        distance_km = round(activity.distance_meters / 1000.0, 2)
    else:
        distance_km = None

    activity_type = activity.type or "Activity"

    return CalendarSession(
        id=activity.id,
        date=activity.start_time.strftime("%Y-%m-%d"),
        time=time_str,
        type=activity_type,
        title=f"{activity_type} - {duration_minutes}min",
        duration_minutes=duration_minutes,
        distance_km=distance_km,
        intensity=intensity,
        status="completed",  # All activities from Strava are completed
        notes=None,
    )


@router.get("/season", response_model=CalendarSeasonResponse)
def get_season(user_id: str = Depends(get_current_user_id)):
    """Get calendar data for the current season from real activities.

    Uses reconciliation to determine authoritative session status.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSeasonResponse with all sessions in the season
    """
    logger.info(f"[CALENDAR] GET /calendar/season called for user_id={user_id}")
    now = datetime.now(timezone.utc)
    season_start = now - timedelta(days=90)
    season_end = now + timedelta(days=90)
    start_date = season_start.date()
    end_date = season_end.date()

    with get_session() as session:
        # Get athlete_id for reconciliation
        athlete_id = _get_athlete_id(session, user_id)

        # Get planned sessions
        planned_sessions = session.execute(
            select(PlannedSession)
            .where(
                PlannedSession.user_id == user_id,
                PlannedSession.date >= season_start,
                PlannedSession.date <= season_end,
            )
            .order_by(PlannedSession.date)
        ).all()

        planned_list = [p[0] for p in planned_sessions]

        # Run reconciliation if we have athlete_id and planned sessions
        reconciliation_map: dict[str, str] = {}
        matched_activity_ids: set[str] = set()

        if athlete_id and planned_list:
            try:
                reconciliation_results = reconcile_calendar(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    start_date=start_date,
                    end_date=end_date,
                )
                # Create mapping from session_id to status
                for result in reconciliation_results:
                    reconciliation_map[result.session_id] = result.status.value
                    if result.matched_activity_id:
                        matched_activity_ids.add(result.matched_activity_id)
            except Exception as e:
                logger.warning(f"[CALENDAR] Reconciliation failed, using planned status: {e!r}")

        # Get completed activities
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.start_time >= season_start,
                Activity.start_time <= season_end,
            )
            .order_by(Activity.start_time)
        ).all()

        # Filter out activities that are matched to planned sessions
        activity_sessions = [_activity_to_session(a[0]) for a in activities if a[0].id not in matched_activity_ids]

        # Convert planned sessions with reconciliation status
        planned_calendar_sessions = [_planned_session_to_calendar(p, reconciliation_map.get(p.id)) for p in planned_list]

        # Combine and sort by date
        all_sessions = activity_sessions + planned_calendar_sessions
        all_sessions.sort(key=lambda s: s.date)

        # Count completed sessions using reconciliation status
        completed = sum(1 for s in planned_calendar_sessions if (reconciliation_map.get(s.id) or s.status) == "completed") + len(
            activity_sessions
        )
        planned = len([s for s in planned_calendar_sessions if (reconciliation_map.get(s.id) or s.status) == "planned"])

    return CalendarSeasonResponse(
        season_start=season_start.strftime("%Y-%m-%d"),
        season_end=season_end.strftime("%Y-%m-%d"),
        sessions=all_sessions,
        total_sessions=len(all_sessions),
        completed_sessions=completed,
        planned_sessions=planned,
    )


@router.get("/week", response_model=CalendarWeekResponse)
def get_week(user_id: str = Depends(get_current_user_id)):
    """Get calendar data for the current week from real activities.

    **Data Source**: Reads from database (not from Strava API).
    Activities are synced incrementally in the background and stored in the database.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarWeekResponse with sessions for this week
    """
    logger.info(f"[CALENDAR] GET /calendar/week called for user_id={user_id}")
    now = datetime.now(timezone.utc)
    # Get Monday of current week
    days_since_monday = now.weekday()
    monday = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

    try:
        with get_session() as session:
            # Get athlete_id for reconciliation
            athlete_id = _get_athlete_id(session, user_id)

            # Get planned sessions
            planned_list = _get_planned_sessions_safe(session, user_id, monday, sunday)

            # Run reconciliation if we have athlete_id and planned sessions
            reconciliation_map, matched_activity_ids = (
                _run_reconciliation_safe(user_id, athlete_id, monday.date(), sunday.date()) if athlete_id and planned_list else ({}, set())
            )

            # Get completed activities
            activity_sessions = _get_activities_safe(session, user_id, monday, sunday, matched_activity_ids)

            # Convert planned sessions with reconciliation status
            planned_calendar_sessions = [_planned_session_to_calendar(p, reconciliation_map.get(p.id)) for p in planned_list]

            # Combine and sort by date
            sessions = activity_sessions + planned_calendar_sessions
            sessions.sort(key=lambda s: (s.date, s.time or ""))

        return CalendarWeekResponse(
            week_start=monday.strftime("%Y-%m-%d"),
            week_end=sunday.strftime("%Y-%m-%d"),
            sessions=sessions,
        )
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.error(
                f"[CALENDAR] Database schema error in /calendar/week. Missing column. Returning empty week: {e!r}",
                exc_info=True,
            )
            # Return empty week instead of 500 - migrations will fix this
            return CalendarWeekResponse(
                week_start=monday.strftime("%Y-%m-%d"),
                week_end=sunday.strftime("%Y-%m-%d"),
                sessions=[],
            )
        logger.error(f"[CALENDAR] Error in /calendar/week: {e!r}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get calendar week: {e!s}") from e


@router.get("/today", response_model=CalendarTodayResponse)
def get_today(user_id: str = Depends(get_current_user_id)):
    """Get calendar data for today from real activities.

    **Data Source**: Reads from database (not from Strava API).
    Activities are synced incrementally in the background and stored in the database.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarTodayResponse with sessions for today
    """
    logger.info(f"[CALENDAR] GET /calendar/today called for user_id={user_id}")
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_end = today + timedelta(days=1) - timedelta(microseconds=1)
    today_str = today.strftime("%Y-%m-%d")

    try:
        with get_session() as session:
            # Get athlete_id for reconciliation
            athlete_id = _get_athlete_id(session, user_id)

            # Get planned sessions
            try:
                planned_sessions = session.execute(
                    select(PlannedSession)
                    .where(
                        PlannedSession.user_id == user_id,
                        PlannedSession.date >= today,
                        PlannedSession.date <= today_end,
                    )
                    .order_by(PlannedSession.date, PlannedSession.time)
                ).all()
                planned_list = [p[0] for p in planned_sessions]
            except Exception as e:
                error_msg = str(e).lower()
                if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
                    logger.warning(f"[CALENDAR] Database schema issue querying planned sessions. Missing column. Returning empty: {e!r}")
                    # Rollback the transaction to prevent "InFailedSqlTransaction" errors on subsequent queries
                    session.rollback()
                    planned_list = []
                else:
                    raise

            # Run reconciliation if we have athlete_id and planned sessions
            reconciliation_map, matched_activity_ids = (
                _run_reconciliation_safe(user_id, athlete_id, today.date(), today_end.date())
                if athlete_id and planned_list
                else ({}, set())
            )

            # Get completed activities
            activity_sessions = _get_activities_safe(session, user_id, today, today_end, matched_activity_ids)

            # Convert planned sessions with reconciliation status
            planned_calendar_sessions = [_planned_session_to_calendar(p, reconciliation_map.get(p.id)) for p in planned_list]

            # Combine and sort by time
            sessions = activity_sessions + planned_calendar_sessions
            sessions.sort(key=lambda s: s.time or "23:59")

        return CalendarTodayResponse(
            date=today_str,
            sessions=sessions,
        )
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.error(
                f"[CALENDAR] Database schema error in /calendar/today. Missing column. Returning empty day: {e!r}",
                exc_info=True,
            )
            # Return empty day instead of 500 - migrations will fix this
            return CalendarTodayResponse(
                date=today_str,
                sessions=[],
            )
        logger.error(f"[CALENDAR] Error in /calendar/today: {e!r}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get calendar today: {e!s}") from e


@router.get("/sessions", response_model=CalendarSessionsResponse)
def get_sessions(limit: int = 50, offset: int = 0, user_id: str = Depends(get_current_user_id)):
    """Get list of calendar sessions from real activities.

    **Data Source**: Reads from database (not from Strava API).
    Activities are synced incrementally in the background and stored in the database.

    Args:
        limit: Maximum number of sessions to return (default: 50)
        offset: Number of sessions to skip (default: 0)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSessionsResponse with list of sessions
    """
    logger.info(f"[CALENDAR] GET /calendar/sessions called for user_id={user_id}: limit={limit}, offset={offset}")

    with get_session() as session:
        # Get athlete_id for reconciliation
        athlete_id = _get_athlete_id(session, user_id)

        # Get planned sessions
        planned_sessions = (
            session.execute(select(PlannedSession).where(PlannedSession.user_id == user_id).order_by(PlannedSession.date.desc()))
            .scalars()
            .all()
        )
        planned_list = list(planned_sessions)

        # Run reconciliation if we have athlete_id and planned sessions
        if athlete_id and planned_list:
            min_date = min(p.date.date() if isinstance(p.date, datetime) else p.date for p in planned_list)
            max_date = max(p.date.date() if isinstance(p.date, datetime) else p.date for p in planned_list)
            reconciliation_map, matched_activity_ids = _run_reconciliation_safe(user_id, athlete_id, min_date, max_date)
        else:
            reconciliation_map, matched_activity_ids = {}, set()

        # Get activities (optimized: uses composite index on user_id + start_time)
        activities = (
            session.execute(select(Activity).where(Activity.user_id == user_id).order_by(Activity.start_time.desc())).scalars().all()
        )
        # Filter out activities that are matched to planned sessions
        activity_sessions = [_activity_to_session(a) for a in activities if a.id not in matched_activity_ids]

        # Convert planned sessions with reconciliation status
        planned_calendar_sessions = [_planned_session_to_calendar(p, reconciliation_map.get(p.id)) for p in planned_list]

        # Combine and sort by date (most recent first)
        all_sessions = activity_sessions + planned_calendar_sessions
        all_sessions.sort(key=lambda s: s.date, reverse=True)

        total = len(all_sessions)
        sessions = all_sessions[offset : offset + limit]

    return CalendarSessionsResponse(
        sessions=sessions,
        total=total,
    )


class UpdateSessionStatusRequest(BaseModel):
    """Request to update a planned session's status."""

    status: str = Field(..., description="New status: planned | completed | skipped | cancelled")
    completed_activity_id: str | None = Field(
        default=None,
        description="ID of the completed activity if status is 'completed'",
    )


@router.patch("/sessions/{session_id}/status", response_model=CalendarSession)
def update_session_status(
    session_id: str,
    request: UpdateSessionStatusRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Update the status of a planned session.

    This endpoint allows marking planned sessions as completed, skipped, or cancelled.
    When marking as completed, you can optionally link it to an actual activity.

    Args:
        session_id: ID of the planned session to update
        request: Update request with new status
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated CalendarSession
    """
    logger.info(f"[CALENDAR] PATCH /calendar/sessions/{session_id}/status called for user_id={user_id}")

    valid_statuses = {"planned", "completed", "skipped", "cancelled"}
    if request.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
        )

    with get_session() as session:
        # Find the planned session
        planned_session = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not planned_session:
            raise HTTPException(status_code=404, detail="Planned session not found")

        # Update status
        planned_session.status = request.status

        # If marking as completed, update completion fields
        if request.status == "completed":
            planned_session.completed = True
            planned_session.completed_at = datetime.now(timezone.utc)
            if request.completed_activity_id:
                planned_session.completed_activity_id = request.completed_activity_id
        else:
            # Reset completion fields if status changes from completed
            planned_session.completed = False
            planned_session.completed_at = None
            planned_session.completed_activity_id = None

        session.commit()
        session.refresh(planned_session)

        return _planned_session_to_calendar(planned_session)
