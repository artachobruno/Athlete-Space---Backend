"""Read-only access to planned activities.

Ground truth of intent - what was planned for the athlete.
"""

from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlannedSession
from app.db.session import get_session
from app.tools.interfaces import PlannedSession as PlannedSessionInterface


def get_planned_activities(
    user_id: str,
    start: date,
    end: date,
    sport: str | None = None,
) -> list[PlannedSessionInterface]:
    """Get planned activities within a date range.

    READ-ONLY: Ground truth of intent.
    No mutation, does NOT infer compliance, pure retrieval only.

    Args:
        user_id: User ID
        start: Start date (inclusive)
        end: End date (inclusive)
        sport: Optional sport filter (e.g., 'run', 'ride')

    Returns:
        List of planned sessions, empty list if none found
    """
    logger.debug(f"Reading planned activities: user_id={user_id}, start={start}, end={end}, sport={sport}")

    with get_session() as session:
        # Convert dates to datetimes for query
        start_datetime = datetime.combine(start, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_datetime = datetime.combine(end, datetime.max.time()).replace(tzinfo=timezone.utc)

        # Build base query
        query = select(PlannedSession).where(
            PlannedSession.user_id == user_id,
            PlannedSession.starts_at >= start_datetime,
            PlannedSession.starts_at <= end_datetime,
        )

        # Add sport filter if provided
        if sport:
            query = query.where(PlannedSession.sport == sport)

        query = query.order_by(PlannedSession.starts_at)

        planned_sessions = list(session.execute(query).scalars().all())

        # Map to PlannedSession interface
        result = []
        for ps in planned_sessions:
            # Get date from starts_at
            session_date = ps.starts_at.date()

            # Convert duration_seconds to minutes
            duration_min = None
            if ps.duration_seconds is not None:
                duration_min = ps.duration_seconds / 60.0

            # Get intensity (prefer intent if intensity is not available)
            intensity = ps.intensity or ps.intent or "unknown"

            # Get target_load from TSS computation (use 0.0 if not available)
            # Note: PlannedSession doesn't have a direct load field,
            # but we can use intent-based defaults or compute from duration
            # For Phase 1, use a placeholder - actual load calculation would need workout data
            target_load = 0.0  # TODO: Compute from workout if available

            result.append(
                PlannedSessionInterface(
                    id=ps.id,
                    date=session_date,
                    sport=ps.sport,
                    intensity=intensity,
                    target_load=target_load,
                    duration_min=duration_min,
                )
            )

        logger.debug(f"Found {len(result)} planned activities")
        return result
