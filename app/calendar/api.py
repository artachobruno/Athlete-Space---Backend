"""Calendar API endpoints with real activity data.

Step 6: Replaces mock data with real activities from database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.calendar.auto_match_service import auto_match_sessions
from app.calendar.reconciliation_service import reconcile_calendar
from app.db.models import Activity, PlannedSession, StravaAccount, User
from app.db.session import get_session
from app.utils.timezone import now_user, to_utc
from app.workouts.models import Workout, WorkoutStep

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _raise_user_not_found() -> None:
    """Raise HTTPException for user not found."""
    raise HTTPException(status_code=404, detail="User not found")


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
) -> list[CalendarSession]:  # CalendarSession is Pydantic schema from schemas.py
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
    """Run reconciliation with safe error handling and auto-matching.

    This function:
    1. Runs reconciliation to find matches
    2. Automatically creates workouts for matches (idempotent)
    3. Returns reconciliation status map

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

        # Auto-match: create workouts for matches
        try:
            auto_match_sessions(user_id=user_id, reconciliation_results=reconciliation_results)
        except Exception as e:
            logger.warning(f"[CALENDAR] Auto-match failed, continuing with reconciliation: {e!r}")

        # Build reconciliation map
        # CRITICAL: Only override DB status if there's a REAL matched activity
        # MISSED status should NOT flip planned → completed (keep it as planned)
        # BUT we DO include MISSED in reconciliation_map so frontend can display MISSED label
        for result in reconciliation_results:
            # Only set reconciliation status if:
            # 1. There's a matched activity (COMPLETED, PARTIAL, SUBSTITUTED have matched_activity_id)
            # 2. OR session is explicitly SKIPPED (user marked it)
            # 3. OR session is MISSED (for frontend label display, but doesn't change DB status)
            if result.matched_activity_id or result.status.value in {"skipped", "missed"}:
                reconciliation_map[result.session_id] = result.status.value
            else:
                # Other statuses (shouldn't happen, but be safe)
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
    # Map PARTIAL and SUBSTITUTED to "completed" for UI display (they have matched activities)
    # Preserve "missed" in status field for frontend to detect and display MISSED label
    status = reconciliation_status if reconciliation_status else planned.status
    if status in {"partial", "substituted"}:
        # These statuses indicate a matched activity, so treat as completed for UI
        status = "completed"
    # Note: "missed" status is preserved here to allow frontend to display MISSED label
    # even though the card kind will remain "planned"

    # Capitalize first letter of session type
    session_type: str = planned.type.capitalize()

    # Convert completed_at to ISO 8601 string (handles timezone-aware datetimes properly)
    completed_at_str = planned.completed_at.isoformat() if planned.completed_at else None

    return CalendarSession(
        id=planned.id,
        date=planned.date.strftime("%Y-%m-%d"),
        time=time_str,
        type=session_type,
        title=planned.title,
        duration_minutes=planned.duration_minutes,
        distance_km=round(planned.distance_km, 2) if planned.distance_km else None,
        intensity=planned.intensity,
        status=status,
        notes=planned.notes,
        workout_id=planned.workout_id,
        completed_activity_id=planned.completed_activity_id,
        completed=planned.completed,
        completed_at=completed_at_str,
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
        workout_id=activity.workout_id,
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
    with get_session() as session:
        # Get user for timezone
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        if not user_result:
            raise HTTPException(status_code=404, detail="User not found")
        user = user_result[0]

        # Get current time in user's timezone
        now_local = now_user(user)

        # Calculate season boundaries in user's timezone
        season_start_local = now_local - timedelta(days=90)
        season_end_local = now_local + timedelta(days=90)

        # Convert to UTC for database queries
        season_start = to_utc(season_start_local)
        season_end = to_utc(season_end_local)
        start_date = season_start_local.date()
        end_date = season_end_local.date()

        logger.info(
            f"[CALENDAR] user={user_id} tz={user.timezone} season={start_date}-{end_date}"
        )

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
        # Use _run_reconciliation_safe to ensure auto-match is called
        reconciliation_map, matched_activity_ids = (
            _run_reconciliation_safe(user_id, athlete_id, start_date, end_date)
            if athlete_id and planned_list
            else ({}, set())
        )

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

        # Count sessions using DB status (raw from database)
        db_planned = sum(1 for p in planned_list if p.status == "planned")
        db_completed = sum(1 for p in planned_list if p.status == "completed")

        # Count sessions using final status (after reconciliation)
        # Reconciliation only overrides if there's a real matched activity
        def get_final_status(session: CalendarSession) -> str:
            """Get final status after reconciliation."""
            return reconciliation_map.get(session.id) or session.status

        # Map reconciliation statuses to final status for counting
        def normalize_final_status(s: CalendarSession) -> str:
            """Normalize final status for counting (partial/substituted -> completed)."""
            final = get_final_status(s)
            if final in {"partial", "substituted"}:
                return "completed"
            return final

        final_planned = sum(1 for s in planned_calendar_sessions if normalize_final_status(s) == "planned")
        final_completed = sum(1 for s in planned_calendar_sessions if normalize_final_status(s) == "completed") + len(activity_sessions)

    return CalendarSeasonResponse(
        season_start=season_start_local.strftime("%Y-%m-%d"),
        season_end=season_end_local.strftime("%Y-%m-%d"),
        sessions=all_sessions,
        total_sessions=len(all_sessions),
        completed_sessions=final_completed,
        planned_sessions=final_planned,
        # Expose DB vs final status for debugging
        completed_sessions_db=db_completed,
        planned_sessions_db=db_planned,
        completed_sessions_final=final_completed,
        planned_sessions_final=final_planned,
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
    try:
        with get_session() as session:
            # Get user for timezone
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                _raise_user_not_found()
            user = user_result[0]

            # Get current time in user's timezone
            now_local = now_user(user)

            # Get Monday of current week in user's timezone
            days_since_monday = now_local.weekday()
            monday_local = (now_local - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            sunday_local = monday_local + timedelta(days=6, hours=23, minutes=59, seconds=59)

            # Convert to UTC for database queries
            monday = to_utc(monday_local)
            sunday = to_utc(sunday_local)

            logger.info(
                f"[CALENDAR] user={user_id} tz={user.timezone} week={monday_local.date()}-{sunday_local.date()}"
            )

            # Get athlete_id for reconciliation
            athlete_id = _get_athlete_id(session, user_id)

            # Get planned sessions
            planned_list = _get_planned_sessions_safe(session, user_id, monday, sunday)

            # Run reconciliation if we have athlete_id and planned sessions
            if athlete_id and planned_list:
                reconciliation_map, matched_activity_ids = _run_reconciliation_safe(
                    user_id, athlete_id, monday_local.date(), sunday_local.date()
                )
            else:
                reconciliation_map, matched_activity_ids = ({}, set())

            # Get completed activities
            activity_sessions = _get_activities_safe(session, user_id, monday, sunday, matched_activity_ids)

            # Convert planned sessions with reconciliation status
            planned_calendar_sessions = [_planned_session_to_calendar(p, reconciliation_map.get(p.id)) for p in planned_list]

            # Combine and sort by date
            sessions = activity_sessions + planned_calendar_sessions
            sessions.sort(key=lambda s: (s.date, s.time or ""))

        return CalendarWeekResponse(
            week_start=monday_local.strftime("%Y-%m-%d"),
            week_end=sunday_local.strftime("%Y-%m-%d"),
            sessions=sessions,
        )
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "does not exist" in error_msg or "undefinedcolumn" in error_msg or "no such column" in error_msg:
            logger.exception(
                f"[CALENDAR] Database schema error in /calendar/week. Missing column. Returning empty week: {e!r}"
            )
            # Return empty week instead of 500 - migrations will fix this
            # Calculate fallback dates using UTC (can't access user timezone in error handler)
            today_utc = datetime.now(timezone.utc).date()
            days_since_monday = today_utc.weekday()
            monday_fallback = today_utc - timedelta(days=days_since_monday)
            sunday_fallback = monday_fallback + timedelta(days=6)
            return CalendarWeekResponse(
                week_start=monday_fallback.strftime("%Y-%m-%d"),
                week_end=sunday_fallback.strftime("%Y-%m-%d"),
                sessions=[],
            )
        logger.exception(f"[CALENDAR] Error in /calendar/week: {e!r}")
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
    try:
        with get_session() as session:
            # Get user for timezone
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                _raise_user_not_found()
            user = user_result[0]

            # Get current time in user's timezone
            now_local = now_user(user)

            # Get today boundaries in user's timezone
            today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end_local = today_local + timedelta(days=1) - timedelta(microseconds=1)

            # Convert to UTC for database queries
            today = to_utc(today_local)
            today_end = to_utc(today_end_local)
            today_str = today_local.strftime("%Y-%m-%d")

            logger.info(f"[CALENDAR] user={user_id} tz={user.timezone} today={today_local.date()}")

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
                _run_reconciliation_safe(user_id, athlete_id, today_local.date(), today_end_local.date())
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
            logger.exception(
                f"[CALENDAR] Database schema error in /calendar/today. Missing column. Returning empty day: {e!r}"
            )
            # Return empty day instead of 500 - migrations will fix this
            # Calculate fallback date using UTC (can't access user timezone in error handler)
            today_fallback = datetime.now(timezone.utc).date()
            today_str_fallback = today_fallback.strftime("%Y-%m-%d")
            return CalendarTodayResponse(
                date=today_str_fallback,
                sessions=[],
            )
        logger.exception(f"[CALENDAR] Error in /calendar/today: {e!r}")
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


@router.get("/sessions/{session_id}", response_model=CalendarSession)
def get_session_by_id(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Get a single planned session by ID.

    This endpoint returns the full details of a planned session, including
    reconciliation status if available. This allows the frontend to display
    session details when clicked, similar to how activities are displayed.

    Args:
        session_id: ID of the planned session to retrieve
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSession with full session details

    Raises:
        HTTPException: If session not found or doesn't belong to user
    """
    logger.info(f"[CALENDAR] GET /calendar/sessions/{session_id} called for user_id={user_id}")

    with get_session() as db_session:
        # Find the planned session
        planned_session = db_session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not planned_session:
            raise HTTPException(status_code=404, detail="Planned session not found")

        # Get athlete_id for reconciliation
        athlete_id = _get_athlete_id(db_session, user_id)

        # Run reconciliation if we have athlete_id
        reconciliation_status: str | None = None
        if athlete_id:
            try:
                # Get the date range for reconciliation (just the session date)
                session_date = planned_session.date.date() if isinstance(planned_session.date, datetime) else planned_session.date
                reconciliation_results = reconcile_calendar(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    start_date=session_date,
                    end_date=session_date,
                )
                # Find the reconciliation result for this session
                for result in reconciliation_results:
                    if result.session_id == session_id:
                        reconciliation_status = result.status.value
                        break
            except Exception as e:
                logger.warning(f"[CALENDAR] Reconciliation failed for session {session_id}: {e!r}")

        return _planned_session_to_calendar(planned_session, reconciliation_status)


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


# Separate router for planned-sessions endpoints (different prefix)
planned_sessions_router = APIRouter(prefix="/planned-sessions", tags=["planned-sessions"])


class UpdatePlannedSessionRequest(BaseModel):
    """Request to update a planned session (for drag/move operations)."""

    date: str | None = Field(default=None, description="New date in YYYY-MM-DD format")
    time: str | None = Field(default=None, description="New time in HH:MM format")


@planned_sessions_router.patch("/{planned_session_id}", response_model=CalendarSession)
def update_planned_session(
    planned_session_id: str,
    request: UpdatePlannedSessionRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Update a planned session (date/time for drag/move operations).

    CANONICAL RULE: Only planned_sessions.id may be mutated.
    Calendar sessions, workouts, and activities are READ-ONLY views.

    This endpoint:
    - ONLY accepts planned_sessions.id
    - NEVER accepts workout_id, calendar_session_id, or activity_id
    - Returns 404 if the ID is not a valid planned_sessions.id

    Args:
        planned_session_id: MUST be a planned_sessions.id (not activity_id, workout_id, etc.)
        request: Update request with new date/time
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated CalendarSession

    Raises:
        HTTPException: 404 if planned_session_id is not a valid planned_sessions.id
    """
    # 🚨 HARD ASSERT: Prevent silent failure if frontend sends None
    if planned_session_id is None:
        raise HTTPException(
            status_code=400,
            detail="planned_session_id must not be None",
        )

    logger.info(
        f"[PLANNED-SESSIONS] PATCH /planned-sessions/{planned_session_id} called for user_id={user_id}"
    )

    with get_session() as session:
        # STRICT VALIDATION: Only query PlannedSession table
        # This ensures we ONLY accept planned_sessions.id
        # If the ID is an activity_id, workout_id, or calendar_session_id, this query will return None
        planned_session = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == planned_session_id,
                PlannedSession.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not planned_session:
            # This correctly rejects:
            # - activity_id (not in planned_sessions table)
            # - workout_id (not in planned_sessions table)
            # - calendar_session_id (not in planned_sessions table)
            # - any other non-planned_sessions.id
            logger.warning(
                f"[PLANNED-SESSIONS] Planned session not found: id={planned_session_id}, user_id={user_id}. "
                "This ID is likely an activity_id, workout_id, or calendar_session_id (all are REJECTED)."
            )
            raise HTTPException(
                status_code=404,
                detail="Planned session not found. Only planned_sessions.id may be mutated.",
            )

        # Update date if provided
        if request.date is not None:
            try:
                # Parse date string (YYYY-MM-DD) using fromisoformat
                new_date = date.fromisoformat(request.date)
                # Convert to datetime (midnight UTC) preserving existing time if any
                existing_time = planned_session.date.time() if isinstance(planned_session.date, datetime) else datetime.min.time()
                planned_session.date = datetime.combine(new_date, existing_time).replace(tzinfo=timezone.utc)
            except ValueError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid date format. Expected YYYY-MM-DD: {e!s}",
                ) from e

        # Update time if provided
        if request.time is not None:
            # Validate time format (HH:MM) by parsing it
            def validate_time_format(time_str: str) -> None:
                """Validate time format (HH:MM) and raise ValueError if invalid."""
                time_parts = time_str.split(":")
                if len(time_parts) != 2:
                    raise ValueError("Time must be in HH:MM format")
                hour = int(time_parts[0])
                minute = int(time_parts[1])
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError("Hour must be 0-23, minute must be 0-59")

            try:
                validate_time_format(request.time)
                planned_session.time = request.time
            except (ValueError, IndexError) as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid time format. Expected HH:MM: {e!s}",
                ) from e

        session.commit()
        session.refresh(planned_session)

        return _planned_session_to_calendar(planned_session)


@planned_sessions_router.delete("/{planned_session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_planned_session(
    planned_session_id: str,
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Delete a planned session.

    CANONICAL RULE: Only planned_sessions.id may be deleted.
    Calendar sessions, workouts, and activities are READ-ONLY views.

    This endpoint:
    - ONLY accepts planned_sessions.id
    - NEVER accepts workout_id, calendar_session_id, or activity_id
    - Returns 404 if the ID is not a valid planned_sessions.id
    - Properly handles cascading deletes for workout and workout_steps

    Args:
        planned_session_id: MUST be a planned_sessions.id (not activity_id, workout_id, etc.)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        None (204 No Content on success)

    Raises:
        HTTPException: 404 if planned_session_id is not a valid planned_sessions.id
    """
    logger.info(
        f"[PLANNED-SESSIONS] DELETE /planned-sessions/{planned_session_id} called for user_id={user_id}"
    )

    with get_session() as session:
        # STRICT VALIDATION: Only query PlannedSession table
        # This ensures we ONLY accept planned_sessions.id
        # If the ID is an activity_id, workout_id, or calendar_session_id, this query will return None
        planned_session = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == planned_session_id,
                PlannedSession.user_id == user_id,
            )
        ).scalar_one_or_none()

        if not planned_session:
            # This correctly rejects:
            # - activity_id (not in planned_sessions table)
            # - workout_id (not in planned_sessions table)
            # - calendar_session_id (not in planned_sessions table)
            # - any other non-planned_sessions.id
            logger.warning(
                f"[PLANNED-SESSIONS] Planned session not found for deletion: id={planned_session_id}, user_id={user_id}. "
                "This ID is likely an activity_id, workout_id, or calendar_session_id (all are REJECTED)."
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Planned session not found",
            )

        # Block deletion if session is locked (has completed_activity_id)
        if planned_session.is_locked:
            logger.warning(
                f"[PLANNED-SESSIONS] Attempted to delete locked session: id={planned_session_id}, user_id={user_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete a session that has been completed and merged with an activity. Unpair the activity first.",
            )

        # Handle cascading deletes for workout and workout_steps
        # Only delete workout if it's not referenced by any activities
        workout_id = planned_session.workout_id
        if workout_id:
            # Check if workout is referenced by any activities
            activity_count = session.execute(
                select(func.count(Activity.id)).where(Activity.workout_id == workout_id)
            ).scalar() or 0

            # Check if workout is referenced by any other planned sessions
            other_planned_count = session.execute(
                select(func.count(PlannedSession.id)).where(
                    PlannedSession.workout_id == workout_id,
                    PlannedSession.id != planned_session_id,
                )
            ).scalar() or 0

            # Only delete workout if it's not referenced elsewhere
            if activity_count == 0 and other_planned_count == 0:
                # Delete workout_steps first (cascade order)
                workout_steps = session.execute(
                    select(WorkoutStep).where(WorkoutStep.workout_id == workout_id)
                ).scalars().all()
                for step in workout_steps:
                    session.delete(step)

                # Delete workout
                workout = session.execute(
                    select(Workout).where(Workout.id == workout_id)
                ).scalar_one_or_none()
                if workout:
                    session.delete(workout)
                    logger.info(
                        f"[PLANNED-SESSIONS] Deleted orphaned workout: workout_id={workout_id}, user_id={user_id}"
                    )
            else:
                logger.info(
                    f"[PLANNED-SESSIONS] Keeping workout (referenced elsewhere): workout_id={workout_id}, "
                    f"activity_count={activity_count}, other_planned_count={other_planned_count}"
                )

        # Delete planned session
        session.delete(planned_session)
        session.commit()
        logger.info(
            f"[PLANNED-SESSIONS] Deleted planned session: id={planned_session_id}, user_id={user_id}"
        )
