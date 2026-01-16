"""Repository functions for MODIFY → day operations.

Handles fetching and persisting modified sessions.
"""

import uuid
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import make_transient

from app.db.models import PlannedSession
from app.db.session import get_session


def get_planned_session_by_date(
    _athlete_id: int,  # Unused: kept for API compatibility
    target_date: date,
    user_id: str | None = None,
) -> PlannedSession | None:
    """Get planned session for a specific date.

    Hard rules:
    - 0 sessions → returns None
    - 1 session → returns that session
    - >1 sessions → raises ValueError (future multi-session support)

    Args:
        athlete_id: Athlete ID
        target_date: Target date
        user_id: Optional user ID for additional filtering

    Returns:
        PlannedSession if found, None if not found

    Raises:
        ValueError: If multiple sessions found for the date
    """
    target_datetime_start = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    target_datetime_end = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    with get_session() as db:
        query = select(PlannedSession).where(
            PlannedSession.starts_at >= target_datetime_start,
            PlannedSession.starts_at <= target_datetime_end,
        )

        if user_id:
            query = query.where(PlannedSession.user_id == user_id)

        query = query.order_by(PlannedSession.starts_at)

        sessions = list(db.execute(query).scalars().all())

        if len(sessions) == 0:
            return None

        if len(sessions) > 1:
            raise ValueError(
                f"Multiple sessions found for date {target_date}. "
                f"Multi-session modification not yet supported."
            )

        return sessions[0]


def save_modified_session(
    original_session: PlannedSession,
    modified_session: PlannedSession,
    modification_reason: str | None = None,
    revision_id: str | None = None,
) -> PlannedSession:
    """Save a modified session as a new planned session.

    Rules:
    - New session becomes active
    - Old session remains (marked as superseded)
    - No hard delete

    Args:
        original_session: Original PlannedSession
        modified_session: Modified PlannedSession (new instance)
        modification_reason: Optional reason for modification
        revision_id: Optional revision ID to link this session to a plan revision

    Returns:
        Saved PlannedSession (new instance)
    """
    with get_session() as db:
        # Mark original as superseded (if we add that field)
        # For now, we just create a new session

        # Detach the modified session from any session state
        # This ensures it's treated as a completely new object
        make_transient(modified_session)

        # Set modification metadata - generate new ID for new session
        new_id = str(uuid.uuid4())
        modified_session.id = new_id
        modified_session.created_at = datetime.now(timezone.utc)
        modified_session.updated_at = datetime.now(timezone.utc)

        # Set revision_id if provided
        if revision_id:
            modified_session.revision_id = revision_id

        # Store reference to original in notes or a metadata field
        if modification_reason:
            existing_notes = modified_session.notes or ""
            modified_session.notes = (
                f"{existing_notes}\n[Modified from {original_session.id}: {modification_reason}]".strip()
                if existing_notes
                else f"[Modified from {original_session.id}: {modification_reason}]"
            )

        # Add as new object
        db.add(modified_session)
        db.flush()  # Flush to get the ID assigned
        db.commit()

        logger.info(
            "Modified session saved",
            original_id=original_session.id,
            new_id=modified_session.id,
            date=modified_session.date.isoformat(),
        )

        return modified_session
