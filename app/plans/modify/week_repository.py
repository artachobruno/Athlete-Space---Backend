"""Repository functions for MODIFY → week operations.

Handles fetching and persisting modified week sessions.
"""

import copy
import uuid
from datetime import date, datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import make_transient

from app.db.models import PlannedSession
from app.db.session import get_session


def get_planned_sessions_in_range(
    athlete_id: int,
    start_date: date,
    end_date: date,
    user_id: str | None = None,
) -> list[PlannedSession]:
    """Get all planned sessions in a date range.

    Args:
        athlete_id: Athlete ID
        start_date: Start date of range (inclusive)
        end_date: End date of range (inclusive)
        user_id: Optional user ID for additional filtering

    Returns:
        List of PlannedSession objects in range, ordered by date
    """
    target_datetime_start = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    target_datetime_end = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)

    with get_session() as db:
        query = select(PlannedSession).where(
            PlannedSession.athlete_id == athlete_id,
            PlannedSession.date >= target_datetime_start,
            PlannedSession.date <= target_datetime_end,
            PlannedSession.completed == False,  # noqa: E712
        )

        if user_id:
            query = query.where(PlannedSession.user_id == user_id)

        query = query.order_by(PlannedSession.date)

        sessions = list(db.execute(query).scalars().all())

    logger.debug(
        f"Found {len(sessions)} sessions in range {start_date} to {end_date}",
        athlete_id=athlete_id,
    )

    return sessions


def clone_session(original: PlannedSession) -> PlannedSession:
    """Clone a PlannedSession for modification.

    Creates a deep copy with new ID and timestamps.

    Args:
        original: Original PlannedSession to clone

    Returns:
        Cloned PlannedSession (detached from session)
    """
    cloned = copy.deepcopy(original)

    # Detach from session state
    make_transient(cloned)

    # Generate new ID
    cloned.id = str(uuid.uuid4())
    cloned.created_at = datetime.now(timezone.utc)
    cloned.updated_at = datetime.now(timezone.utc)

    return cloned


def save_modified_sessions(
    original_sessions: list[PlannedSession],
    modified_sessions: list[PlannedSession],
    modification_reason: str | None = None,
    revision_id: str | None = None,
) -> list[PlannedSession]:
    """Save modified sessions as new planned sessions.

    Rules:
    - New sessions become active
    - Old sessions remain (marked as superseded in notes)
    - No hard delete

    Args:
        original_sessions: Original PlannedSession objects
        modified_sessions: Modified PlannedSession objects (new instances)
        modification_reason: Optional reason for modification
        revision_id: Optional revision ID to link these sessions to a plan revision

    Returns:
        List of saved PlannedSession objects
    """
    with get_session() as db:
        saved_sessions = []

        for modified_session in modified_sessions:
            # Ensure detached
            make_transient(modified_session)

            # Find original if available
            original = next(
                (s for s in original_sessions if s.id == modified_session.id or s.date == modified_session.date),
                None,
            )

            # Add modification metadata to notes
            if modification_reason:
                existing_notes = modified_session.notes or ""
                if original:
                    modified_session.notes = (
                        f"{existing_notes}\n[Modified from {original.id}: {modification_reason}]".strip()
                        if existing_notes
                        else f"[Modified from {original.id}: {modification_reason}]"
                    )
                else:
                    modified_session.notes = (
                        f"{existing_notes}\n[Modified: {modification_reason}]".strip()
                        if existing_notes
                        else f"[Modified: {modification_reason}]"
                    )

            # Set revision_id if provided
            if revision_id:
                modified_session.revision_id = revision_id

            # Add as new object
            db.add(modified_session)
            saved_sessions.append(modified_session)

        # Flush to get IDs assigned
        db.flush()
        db.commit()

        logger.info(
            "Modified week sessions saved",
            original_count=len(original_sessions),
            new_count=len(saved_sessions),
            reason=modification_reason,
        )

    return saved_sessions
