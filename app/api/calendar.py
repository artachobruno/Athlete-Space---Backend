"""Calendar API endpoints - Phase 1: Mock data implementation.

These endpoints return mock data to establish the API contract before
implementing real data logic.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from loguru import logger

from app.api.schemas import (
    CalendarSeasonResponse,
    CalendarSession,
    CalendarSessionsResponse,
    CalendarTodayResponse,
    CalendarWeekResponse,
)
from app.core.auth import get_current_user

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _generate_mock_sessions(start_date: datetime, count: int, user_id: str) -> list[CalendarSession]:
    """Generate mock calendar sessions.

    Args:
        start_date: Starting date for sessions
        count: Number of sessions to generate
        user_id: User ID to make sessions user-specific

    Returns:
        List of mock CalendarSession objects
    """
    sessions = []
    activity_types = ["Run", "Bike", "Swim", "Strength", "Yoga"]
    intensities = ["easy", "moderate", "hard"]
    statuses = ["planned", "completed", "skipped"]
    titles = [
        "Morning Run",
        "Long Run",
        "Interval Training",
        "Recovery Run",
        "Tempo Run",
        "Base Ride",
        "Swim Session",
        "Strength Training",
        "Yoga Flow",
    ]

    # Use user_id hash to make sessions deterministic per user
    user_hash = hash(user_id) % 1000

    current_date = start_date
    for i in range(count):
        session_date = current_date + timedelta(days=(i + user_hash) % 14)
        sessions.append(
            CalendarSession(
                id=f"session_{user_id}_{i}",
                date=session_date.strftime("%Y-%m-%d"),
                time="07:00" if (i + user_hash) % 2 == 0 else "18:00",
                type=activity_types[(i + user_hash) % len(activity_types)],
                title=titles[(i + user_hash) % len(titles)],
                duration_minutes=30 + ((i + user_hash) * 15) % 120,
                distance_km=(
                    5.0 + ((i + user_hash) * 2.5) % 25.0
                    if activity_types[(i + user_hash) % len(activity_types)] in {"Run", "Bike"}
                    else None
                ),
                intensity=intensities[(i + user_hash) % len(intensities)],
                status=statuses[(i + user_hash) % len(statuses)],
                notes=f"Mock session {i + 1} for user {user_id[:8]}" if i % 3 == 0 else None,
            )
        )
    return sessions


@router.get("/season", response_model=CalendarSeasonResponse)
def get_season(user_id: str = Depends(get_current_user)):
    """Get calendar data for the current season.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSeasonResponse with all sessions in the season
    """
    logger.info(f"[API] /calendar/season endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)
    season_start = now - timedelta(days=90)
    season_end = now + timedelta(days=90)

    sessions = _generate_mock_sessions(season_start, 45, user_id)
    completed = sum(1 for s in sessions if s.status == "completed")
    planned = sum(1 for s in sessions if s.status == "planned")

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
    """Get calendar data for the current week.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarWeekResponse with sessions for this week
    """
    logger.info(f"[API] /calendar/week endpoint called for user_id={user_id}")
    now = datetime.now(timezone.utc)
    # Get Monday of current week
    days_since_monday = now.weekday()
    monday = now - timedelta(days=days_since_monday)
    sunday = monday + timedelta(days=6)

    sessions = _generate_mock_sessions(monday, 7, user_id)
    # Filter to only sessions in the current week
    week_sessions = [s for s in sessions if monday.strftime("%Y-%m-%d") <= s.date <= sunday.strftime("%Y-%m-%d")]

    return CalendarWeekResponse(
        week_start=monday.strftime("%Y-%m-%d"),
        week_end=sunday.strftime("%Y-%m-%d"),
        sessions=week_sessions,
    )


@router.get("/today", response_model=CalendarTodayResponse)
def get_today(user_id: str = Depends(get_current_user)):
    """Get calendar data for today.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarTodayResponse with sessions for today
    """
    logger.info(f"[API] /calendar/today endpoint called for user_id={user_id}")
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")

    sessions = _generate_mock_sessions(today, 3, user_id)
    # Filter to only today's sessions
    today_sessions = [s for s in sessions if s.date == today_str]

    return CalendarTodayResponse(
        date=today_str,
        sessions=today_sessions,
    )


@router.get("/sessions", response_model=CalendarSessionsResponse)
def get_sessions(limit: int = 50, offset: int = 0, user_id: str = Depends(get_current_user)):
    """Get list of calendar sessions.

    Args:
        limit: Maximum number of sessions to return (default: 50)
        offset: Number of sessions to skip (default: 0)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        CalendarSessionsResponse with list of sessions
    """
    logger.info(f"[API] /calendar/sessions endpoint called for user_id={user_id}: limit={limit}, offset={offset}")
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=30)

    sessions = _generate_mock_sessions(start_date, 30, user_id)
    total = len(sessions)

    # Apply pagination
    paginated_sessions = sessions[offset : offset + limit]

    return CalendarSessionsResponse(
        sessions=paginated_sessions,
        total=total,
    )
