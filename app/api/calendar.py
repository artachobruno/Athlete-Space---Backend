"""Calendar API endpoints with real activity data.

Step 6: Replaces mock data with real activities from database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import select

from app.api.schemas import (
    CalendarSeasonResponse,
    CalendarSession,
    CalendarSessionsResponse,
    CalendarTodayResponse,
    CalendarWeekResponse,
)
from app.core.auth import get_current_user
from app.state.db import get_session
from app.state.models import Activity

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _activity_to_session(activity: Activity) -> CalendarSession:
    """Convert Activity to CalendarSession.

    Args:
        activity: Activity record

    Returns:
        CalendarSession object
    """
    # Determine intensity based on duration
    duration_hours = activity.duration_seconds / 3600.0

    if duration_hours > 1.5:
        intensity = "easy"
    elif duration_hours > 0.75:
        intensity = "moderate"
    else:
        intensity = "hard"

    # Format time
    time_str = activity.start_time.strftime("%H:%M")

    # Determine distance in km
    distance_km = activity.distance_meters / 1000.0 if activity.distance_meters > 0 else None

    return CalendarSession(
        id=activity.id,
        date=activity.start_time.strftime("%Y-%m-%d"),
        time=time_str,
        type=activity.type,
        title=f"{activity.type} - {int(activity.duration_seconds / 60)}min",
        duration_minutes=int(activity.duration_seconds / 60),
        distance_km=round(distance_km, 2) if distance_km else None,
        intensity=intensity,
        status="completed",  # All activities from Strava are completed
        notes=None,
    )


@router.get("/season", response_model=CalendarSeasonResponse)
def get_season(user_id: str = Depends(get_current_user)):
    """Get calendar data for the current season from real activities.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSeasonResponse with all sessions in the season
    """
    logger.info(f"[CALENDAR] GET /calendar/season called for user_id={user_id}")
    now = datetime.now(timezone.utc)
    season_start = now - timedelta(days=90)
    season_end = now + timedelta(days=90)

    with get_session() as session:
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.start_time >= season_start,
                Activity.start_time <= season_end,
            )
            .order_by(Activity.start_time)
        ).all()

        sessions = [_activity_to_session(a[0]) for a in activities]
        completed = len(sessions)  # All activities are completed
        planned = 0  # No planned sessions yet

    return CalendarSeasonResponse(
        season_start=season_start.strftime("%Y-%m-%d"),
        season_end=season_end.strftime("%Y-%m-%d"),
        sessions=sessions,
        total_sessions=len(sessions),
        completed_sessions=completed,
        planned_sessions=planned,
    )


@router.get("/week", response_model=CalendarWeekResponse)
def get_week(user_id: str = Depends(get_current_user)):
    """Get calendar data for the current week from real activities.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarWeekResponse with sessions for this week
    """
    logger.info(f"[CALENDAR] GET /calendar/week called for user_id={user_id}")
    now = datetime.now(timezone.utc)
    # Get Monday of current week
    days_since_monday = now.weekday()
    monday = now - timedelta(days=days_since_monday)
    sunday = monday + timedelta(days=6)

    with get_session() as session:
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.start_time >= monday,
                Activity.start_time <= sunday,
            )
            .order_by(Activity.start_time)
        ).all()

        sessions = [_activity_to_session(a[0]) for a in activities]

    return CalendarWeekResponse(
        week_start=monday.strftime("%Y-%m-%d"),
        week_end=sunday.strftime("%Y-%m-%d"),
        sessions=sessions,
    )


@router.get("/today", response_model=CalendarTodayResponse)
def get_today(user_id: str = Depends(get_current_user)):
    """Get calendar data for today from real activities.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarTodayResponse with sessions for today
    """
    logger.info(f"[CALENDAR] GET /calendar/today called for user_id={user_id}")
    today = datetime.now(timezone.utc)
    today_start = today.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today.replace(hour=23, minute=59, second=59, microsecond=999999)
    today_str = today.strftime("%Y-%m-%d")

    with get_session() as session:
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.start_time >= today_start,
                Activity.start_time <= today_end,
            )
            .order_by(Activity.start_time)
        ).all()

        sessions = [_activity_to_session(a[0]) for a in activities]

    return CalendarTodayResponse(
        date=today_str,
        sessions=sessions,
    )


@router.get("/sessions", response_model=CalendarSessionsResponse)
def get_sessions(limit: int = 50, offset: int = 0, user_id: str = Depends(get_current_user)):
    """Get list of calendar sessions from real activities.

    Args:
        limit: Maximum number of sessions to return (default: 50)
        offset: Number of sessions to skip (default: 0)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSessionsResponse with list of sessions
    """
    logger.info(f"[CALENDAR] GET /calendar/sessions called for user_id={user_id}: limit={limit}, offset={offset}")

    with get_session() as session:
        # Get total count
        total_result = session.execute(select(Activity).where(Activity.user_id == user_id))
        total = len(list(total_result))

        # Get paginated activities
        activities = session.execute(
            select(Activity).where(Activity.user_id == user_id).order_by(Activity.start_time.desc()).limit(limit).offset(offset)
        ).all()

        sessions = [_activity_to_session(a[0]) for a in activities]

    return CalendarSessionsResponse(
        sessions=sessions,
        total=total,
    )
