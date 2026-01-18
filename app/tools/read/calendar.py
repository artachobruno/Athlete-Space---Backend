"""Read-only access to calendar events.

All non-training + training calendar visibility.
"""

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlannedSession
from app.db.session import get_session
from app.tools.interfaces import CalendarEvent


def get_calendar_events(
    user_id: str,
    start: datetime,
    end: datetime,
) -> list[CalendarEvent]:
    """Get calendar events within a time window.

    READ-ONLY: All non-training + training calendar visibility.
    Includes planned sessions from training and other calendar sources.

    Args:
        user_id: User ID
        start: Start datetime (inclusive)
        end: End datetime (inclusive)

    Returns:
        List of calendar events, empty list if none found
    """
    logger.debug(f"Reading calendar events: user_id={user_id}, start={start}, end={end}")

    events: list[CalendarEvent] = []

    with get_session() as session:
        # Get planned training sessions
        planned_query = select(PlannedSession).where(
            PlannedSession.user_id == user_id,
            PlannedSession.starts_at >= start,
            PlannedSession.starts_at <= end,
        )
        planned_sessions = list(session.execute(planned_query).scalars().all())

        for ps in planned_sessions:
            # Use ends_at if available, otherwise estimate from duration
            end_time = ps.ends_at
            if end_time is None and ps.duration_seconds:
                end_time = ps.starts_at.replace(microsecond=0) + timedelta(seconds=ps.duration_seconds)
            elif end_time is None:
                # Default to 1 hour if no duration
                end_time = ps.starts_at.replace(microsecond=0) + timedelta(hours=1)

            title = ps.title or ps.sport or "Training"
            events.append(
                CalendarEvent(
                    id=ps.id,
                    start_time=ps.starts_at,
                    end_time=end_time,
                    title=title,
                    source="training",
                )
            )

        # TODO: In the future, query other calendar sources (Google Calendar, etc.)
        # For Phase 1, only training events are included

        logger.debug(f"Found {len(events)} calendar events")
        return events
