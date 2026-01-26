"""Compliance Tracking - Phase 6A.

Track what actually happened vs what was planned.
Core value: Manual edits always win, history is never overwritten.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlannedSession
from app.db.session import get_session


@dataclass(frozen=True)
class SessionCompliance:
    """Compliance record for a single session.

    Attributes:
        session_id: Session identifier
        status: Compliance status (scheduled, completed, skipped, modified)
        completed_duration_min: Actual duration if completed (None if not completed)
    """

    session_id: str
    status: Literal["scheduled", "completed", "skipped", "modified"]
    completed_duration_min: int | None = None


def get_session_compliance(session_id: str, user_id: str) -> SessionCompliance | None:
    """Get compliance record for a session.

    Args:
        session_id: Session identifier
        user_id: User ID

    Returns:
        SessionCompliance or None if session not found
    """
    with get_session() as session:
        planned = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        ).first()

        if not planned:
            return None

        # Determine status from PlannedSession
        if planned[0].completed:
            return SessionCompliance(
                session_id=session_id,
                status="completed",
                completed_duration_min=planned[0].duration_minutes,
            )
        if planned[0].status == "skipped" or planned[0].status == "deleted":
            return SessionCompliance(
                session_id=session_id,
                status="skipped",
                completed_duration_min=None,
            )
        # Check if modified (has updated_at different from created_at and status is "planned")
        if planned[0].updated_at != planned[0].created_at and planned[0].status == "planned":
            return SessionCompliance(
                session_id=session_id,
                status="modified",
                completed_duration_min=None,
            )
        return SessionCompliance(
            session_id=session_id,
            status="scheduled",
            completed_duration_min=None,
        )


def record_manual_edit(session_id: str, user_id: str) -> None:
    """Record a manual edit to a session.

    Phase 6A: Manual edits always win - mark session as modified.

    Args:
        session_id: Session identifier
        user_id: User ID
    """
    logger.info(
        "[COMPLIANCE] Manual edit recorded",
        session_id=session_id,
        user_id=user_id,
    )

    with get_session() as session:
        planned = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        ).first()

        if planned:
            # Update updated_at to mark as modified
            planned[0].updated_at = datetime.now(timezone.utc)
            session.commit()


def record_completion(
    session_id: str,
    user_id: str,
    completed_duration_min: int | None = None,
) -> None:
    """Record session completion.

    Args:
        session_id: Session identifier
        user_id: User ID
        completed_duration_min: Actual duration (optional)
    """
    logger.info(
        "[COMPLIANCE] Session completion recorded",
        session_id=session_id,
        user_id=user_id,
        completed_duration_min=completed_duration_min,
    )

    with get_session() as session:
        planned = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        ).first()

        if planned:
            planned[0].completed = True
            # Use setattr to handle case where column doesn't exist in database (migration pending)
            if hasattr(planned[0], "completed_at"):
                planned[0].completed_at = datetime.now(timezone.utc)
            # PHASE 1.3: Execution outcome is derived via execution_state helper, not stored
            # Do not write execution state to planned_sessions.status
            if completed_duration_min is not None:
                planned[0].duration_minutes = completed_duration_min
            session.commit()


def record_skip(session_id: str, user_id: str) -> None:
    """Record session skip.

    Args:
        session_id: Session identifier
        user_id: User ID
    """
    logger.info(
        "[COMPLIANCE] Session skip recorded",
        session_id=session_id,
        user_id=user_id,
    )

    with get_session() as session:
        planned = session.execute(
            select(PlannedSession).where(
                PlannedSession.id == session_id,
                PlannedSession.user_id == user_id,
            )
        ).first()

        if planned:
            # PHASE 1.3: Execution outcome is derived via execution_state helper, not stored
            # Do not write execution state to planned_sessions.status
            # Lifecycle status remains 'scheduled' - skip is an execution outcome, not a planning change
            session.commit()
