"""Calendar helper functions for materializing calendar sessions from activities."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, CalendarSession


def ensure_calendar_session_for_activity(session: Session, activity: Activity) -> CalendarSession:
    """Ensure a CalendarSession exists for a completed activity.

    Creates a CalendarSession if it doesn't exist, or returns the existing one.
    Enforces the invariant: Every completed activity must have exactly one CalendarSession.

    Args:
        session: Database session
        activity: Activity record to create calendar session for

    Returns:
        CalendarSession record (existing or newly created)

    Raises:
        ValueError: If activity is missing required fields
    """
    # Check if calendar session already exists
    existing = session.execute(
        select(CalendarSession).where(CalendarSession.activity_id == activity.id)
    ).first()

    if existing:
        return existing[0]

    # Validate required fields
    if not activity.start_time:
        raise ValueError(f"Activity {activity.id} missing start_time")

    # Calculate duration in minutes
    duration_minutes = None
    if activity.duration_seconds is not None:
        duration_minutes = int(activity.duration_seconds // 60)

    # Calculate distance in km
    distance_km = None
    if activity.distance_meters is not None and activity.distance_meters > 0:
        distance_km = round(activity.distance_meters / 1000.0, 2)

    # Determine activity type
    activity_type = activity.type or "Activity"

    # Generate title
    if duration_minutes:
        title = f"{activity_type} - {duration_minutes}min"
    else:
        title = activity_type

    # Create calendar session
    calendar_session = CalendarSession(
        user_id=activity.user_id,
        date=activity.start_time,
        type=activity_type,
        title=title,
        duration_minutes=duration_minutes,
        distance_km=distance_km,
        status="completed",
        activity_id=activity.id,
    )

    session.add(calendar_session)
    logger.debug(f"[CALENDAR] Created calendar session for activity {activity.id}")
    return calendar_session
