"""Read-only access to completed activities.

Source of truth for executed workouts.
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, SessionLink
from app.db.session import get_session
from app.tools.interfaces import CompletedActivity


def get_completed_activities(
    user_id: str,
    start: datetime,
    end: datetime,
    sport: str | None = None,
) -> list[CompletedActivity]:
    """Get completed activities within a time window.

    READ-ONLY: Source of truth for executed workouts.
    No writes, no modifications.

    Args:
        user_id: User ID
        start: Start datetime (inclusive)
        end: End datetime (inclusive)
        sport: Optional sport filter (e.g., 'run', 'ride')

    Returns:
        List of completed activities, empty list if none found
    """
    logger.debug(
        f"Reading completed activities: user_id={user_id}, start={start}, end={end}, sport={sport}"
    )

    with get_session() as session:
        # Build base query
        query = select(Activity).where(
            Activity.user_id == user_id,
            Activity.starts_at >= start,
            Activity.starts_at <= end,
        )

        # Add sport filter if provided
        if sport:
            query = query.where(Activity.sport == sport)

        query = query.order_by(Activity.starts_at)

        activities = list(session.execute(query).scalars().all())

        # Get planned_session_id mappings from SessionLink
        activity_ids = [act.id for act in activities]
        planned_session_map: dict[str, str] = {}
        if activity_ids:
            links_query = select(SessionLink).where(SessionLink.activity_id.in_(activity_ids))
            links = session.execute(links_query).scalars().all()
            for link in links:
                planned_session_map[link.activity_id] = link.planned_session_id

        # Map to CompletedActivity
        result = []
        for activity in activities:
            # Convert duration_seconds to minutes
            duration_min = activity.duration_seconds / 60.0 if activity.duration_seconds else 0.0

            # Convert distance_meters to km
            distance_km = None
            if activity.distance_meters is not None:
                distance_km = activity.distance_meters / 1000.0

            # Get load from TSS (use 0.0 if not available)
            load = activity.tss if activity.tss is not None else 0.0

            # Get planned_session_id from SessionLink
            planned_session_id = planned_session_map.get(activity.id)

            result.append(
                CompletedActivity(
                    id=activity.id,
                    sport=activity.sport,
                    start_time=activity.starts_at,
                    duration_min=duration_min,
                    distance_km=distance_km,
                    load=load,
                    planned_session_id=planned_session_id,
                )
            )

        logger.debug(f"Found {len(result)} completed activities")
        return result
