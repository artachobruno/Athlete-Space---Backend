"""Database adapter for semantic tools.

This adapter wraps all direct database access. Semantic tools should never
directly import or call database session code.
"""

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

# Import models here - these are implementation details
from app.db.models import PlannedSession
from app.db.session import get_session


async def get_planned_sessions_db(  # noqa: RUF029
    user_id: str,
    athlete_id: int,
    start_date: Any,
    end_date: Any | None = None,
) -> list[dict[str, Any]]:
    """Get planned sessions from database via adapter.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        start_date: Start date
        end_date: End date (optional)

    Returns:
        List of planned session dictionaries
    """
    logger.debug(
        "DB adapter: get_planned_sessions",
        user_id=user_id,
        athlete_id=athlete_id,
        start_date=start_date,
        end_date=end_date,
    )

    with get_session() as session:
        query = select(PlannedSession).where(PlannedSession.user_id == user_id)
        if start_date:
            query = query.where(PlannedSession.starts_at >= start_date)
        if end_date:
            query = query.where(PlannedSession.starts_at <= end_date)

        sessions = list(session.execute(query).scalars().all())

        return [
            {
                "id": str(session.id),
                "date": session.starts_at.date(),
                "sport": session.sport or "running",
                "intensity": session.intensity or "easy",
                "target_load": float(session.target_load) if session.target_load else 0.0,
                "duration_min": float(session.duration_minutes) if session.duration_minutes else None,
            }
            for session in sessions
        ]
