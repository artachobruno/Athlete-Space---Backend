"""SessionLink helper functions for schema v2 pairing.

This module provides the only way to create, update, or delete links between
planned_sessions and activities. All pairing logic must use these functions.

Schema v2 enforces one-to-one relationships:
- One planned_session can link to at most one activity (via UNIQUE on planned_session_id)
- One activity can link to at most one planned_session (via UNIQUE on activity_id)
"""

import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.exc import InternalError
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession, SessionLink


def get_link_for_planned(session: Session, planned_session_id: str) -> SessionLink | None:
    """Get the session link for a planned session.

    Args:
        session: Database session
        planned_session_id: Planned session ID

    Returns:
        SessionLink if found, None otherwise
    """
    try:
        return session.execute(
            select(SessionLink).where(SessionLink.planned_session_id == planned_session_id)
        ).scalar_one_or_none()
    except InternalError as e:
        # If transaction is aborted, rollback and return None
        # This can happen if a previous query in the same transaction failed
        error_str = str(e).lower()
        if "current transaction is aborted" in error_str or "in failed sql transaction" in error_str:
            logger.warning(
                f"Transaction aborted when querying session link for planned_session_id={planned_session_id}, "
                f"rolling back and returning None"
            )
            session.rollback()
            return None
        raise


def get_link_for_activity(session: Session, activity_id: str) -> SessionLink | None:
    """Get the session link for an activity.

    Args:
        session: Database session
        activity_id: Activity ID

    Returns:
        SessionLink if found, None otherwise
    """
    try:
        return session.execute(
            select(SessionLink).where(SessionLink.activity_id == activity_id)
        ).scalar_one_or_none()
    except InternalError as e:
        # If transaction is aborted, rollback and return None
        # This can happen if a previous query in the same transaction failed
        error_str = str(e).lower()
        if "current transaction is aborted" in error_str or "in failed sql transaction" in error_str:
            logger.warning(
                f"Transaction aborted when querying session link for activity_id={activity_id}, "
                f"rolling back and returning None"
            )
            session.rollback()
            return None
        raise


def upsert_link(
    session: Session,
    user_id: str,
    planned_session_id: str,
    activity_id: str,
    status: str,
    method: str,
    confidence: float | None = None,
    notes: str | None = None,
    match_reason: dict | None = None,
    deltas: dict | None = None,
    resolved_at: datetime | None = None,
) -> SessionLink:
    """Create or update a link between a planned session and an activity.

    This function enforces the one-to-one relationship:
    - Deletes any existing link for planned_session_id (if it points elsewhere)
    - Deletes any existing link for activity_id (if it points elsewhere)
    - Then inserts the new link

    All operations are done within the same transaction.

    Args:
        session: Database session
        user_id: User ID
        planned_session_id: Planned session ID
        activity_id: Activity ID
        status: Link status ('proposed', 'confirmed', 'rejected')
        method: Pairing method ('auto', 'manual')
        confidence: Confidence score (0.0-1.0, optional)
        notes: Optional notes
        match_reason: Optional dictionary with match reason details
        deltas: Optional dictionary with delta values between planned and actual
        resolved_at: Optional timestamp when the link was resolved

    Returns:
        The created or updated SessionLink

    Raises:
        ValueError: If status or method is invalid
    """
    # Validate status and method
    valid_statuses = {"proposed", "confirmed", "rejected"}
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

    valid_methods = {"auto", "manual"}
    if method not in valid_methods:
        raise ValueError(f"Invalid method: {method}. Must be one of {valid_methods}")

    # Verify planned_session and activity exist and belong to user
    planned = session.execute(
        select(PlannedSession).where(
            PlannedSession.id == planned_session_id, PlannedSession.user_id == user_id
        )
    ).scalar_one_or_none()
    if not planned:
        raise ValueError(f"Planned session not found: {planned_session_id} for user {user_id}")

    activity = session.execute(
        select(Activity).where(Activity.id == activity_id, Activity.user_id == user_id)
    ).scalar_one_or_none()
    if not activity:
        raise ValueError(f"Activity not found: {activity_id} for user {user_id}")

    # Delete any existing link for planned_session_id (if it points elsewhere)
    existing_planned_link = get_link_for_planned(session, planned_session_id)
    if existing_planned_link and existing_planned_link.activity_id != activity_id:
        logger.debug(
            "Removing existing link for planned_session",
            planned_session_id=planned_session_id,
            old_activity_id=existing_planned_link.activity_id,
        )
        session.delete(existing_planned_link)

    # Delete any existing link for activity_id (if it points elsewhere)
    existing_activity_link = get_link_for_activity(session, activity_id)
    if existing_activity_link and existing_activity_link.planned_session_id != planned_session_id:
        logger.debug(
            "Removing existing link for activity",
            activity_id=activity_id,
            old_planned_session_id=existing_activity_link.planned_session_id,
        )
        session.delete(existing_activity_link)

    # Flush to ensure deletes are applied before insert
    session.flush()

    # Check if link already exists (same planned + activity)
    existing_link = session.execute(
        select(SessionLink).where(
            SessionLink.planned_session_id == planned_session_id,
            SessionLink.activity_id == activity_id,
        )
    ).scalar_one_or_none()

    if existing_link:
        # Update existing link
        existing_link.status = status
        existing_link.method = method
        existing_link.confidence = confidence
        existing_link.notes = notes
        if match_reason is not None:
            existing_link.match_reason = match_reason
        if deltas is not None:
            existing_link.deltas = deltas
        if resolved_at is not None:
            existing_link.resolved_at = resolved_at
        existing_link.updated_at = datetime.now(timezone.utc)

        logger.debug(
            "Updated existing session link",
            link_id=existing_link.id,
            planned_session_id=planned_session_id,
            activity_id=activity_id,
            status=status,
            method=method,
        )

        return existing_link

    # Create new link
    new_link = SessionLink(
        id=str(uuid.uuid4()),
        user_id=user_id,
        planned_session_id=planned_session_id,
        activity_id=activity_id,
        status=status,
        method=method,
        confidence=confidence,
        notes=notes,
        match_reason=match_reason,
        deltas=deltas,
        resolved_at=resolved_at,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    session.add(new_link)

    logger.debug(
        "Created new session link",
        link_id=new_link.id,
        planned_session_id=planned_session_id,
        activity_id=activity_id,
        status=status,
        method=method,
    )

    return new_link


def unlink_by_planned(session: Session, planned_session_id: str, reason: str | None = None) -> bool:
    """Unlink a planned session from its activity.

    Args:
        session: Database session
        planned_session_id: Planned session ID
        reason: Optional reason for unlinking (for logging)

    Returns:
        True if a link was found and deleted, False otherwise
    """
    link = get_link_for_planned(session, planned_session_id)
    if link:
        logger.debug(
            "Unlinking planned session",
            planned_session_id=planned_session_id,
            activity_id=link.activity_id,
            reason=reason,
        )
        session.delete(link)
        session.flush()
        return True

    logger.debug("No link found for planned session", planned_session_id=planned_session_id)
    return False


def unlink_by_activity(session: Session, activity_id: str, reason: str | None = None) -> bool:
    """Unlink an activity from its planned session.

    Args:
        session: Database session
        activity_id: Activity ID
        reason: Optional reason for unlinking (for logging)

    Returns:
        True if a link was found and deleted, False otherwise
    """
    link = get_link_for_activity(session, activity_id)
    if link:
        logger.debug(
            "Unlinking activity",
            activity_id=activity_id,
            planned_session_id=link.planned_session_id,
            reason=reason,
        )
        session.delete(link)
        session.flush()
        return True

    logger.debug("No link found for activity", activity_id=activity_id)
    return False
