"""Manual pairing service for explicit user-controlled pairing.

This service provides explicit APIs to manually merge or unmerge
planned sessions with executed activities. Manual actions override
auto-pairing and are fully auditable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy.orm import Session

from app.db.models import Activity, PairingDecision, PlannedSession


def _log_decision(
    *,
    user_id: str,
    activity: Activity | None,
    planned: PlannedSession | None,
    decision: str,
    reason: str,
    session: Session,
) -> None:
    """Log pairing decision to audit table.

    Args:
        user_id: User ID
        activity: Activity (may be None)
        planned: Planned session (may be None)
        decision: Decision type (manual_pair, manual_unpair)
        reason: Reason for decision
        session: Database session
    """
    activity_id = activity.id if activity else None
    planned_session_id = planned.id if planned else None

    pairing_decision = PairingDecision(
        user_id=user_id,
        planned_session_id=planned_session_id,
        activity_id=activity_id,
        decision=decision,
        duration_diff_pct=None,
        reason=reason,
        created_at=datetime.now(timezone.utc),
    )

    session.add(pairing_decision)


def manual_pair(
    *,
    activity_id: str,
    planned_session_id: str,
    user_id: str,
    session: Session,
) -> None:
    """Explicitly pair an activity with a planned session.

    Clears any existing links first (idempotent).

    Args:
        activity_id: Activity ID
        planned_session_id: Planned session ID
        user_id: User ID (for ownership validation)
        session: Database session

    Raises:
        HTTPException: If activity or planned session not found
        HTTPException: If ownership validation fails
    """
    activity = session.get(Activity, activity_id)
    plan = session.get(PlannedSession, planned_session_id)

    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activity {activity_id} not found",
        )

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Planned session {planned_session_id} not found",
        )

    if activity.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity does not belong to user",
        )

    if plan.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Planned session does not belong to user",
        )

    with session.begin():
        # Clear existing activity → plan link
        if activity.planned_session_id:
            old_plan = session.get(PlannedSession, activity.planned_session_id)
            if old_plan:
                old_plan.completed_activity_id = None

        # Clear existing plan → activity link
        if plan.completed_activity_id:
            old_activity = session.get(Activity, plan.completed_activity_id)
            if old_activity:
                old_activity.planned_session_id = None

        # Set new pairing
        activity.planned_session_id = plan.id
        plan.completed_activity_id = activity.id

        _log_decision(
            user_id=user_id,
            activity=activity,
            planned=plan,
            decision="manual_pair",
            reason="user_action",
            session=session,
        )

        logger.info(
            f"Manually paired activity {activity_id} with planned session {planned_session_id}",
            user_id=user_id,
        )


def manual_unpair(
    *,
    activity_id: str,
    user_id: str,
    session: Session,
) -> None:
    """Explicitly remove pairing between activity and planned session.

    Args:
        activity_id: Activity ID
        user_id: User ID (for ownership validation)
        session: Database session

    Raises:
        HTTPException: If activity not found
        HTTPException: If ownership validation fails
    """
    activity = session.get(Activity, activity_id)

    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Activity {activity_id} not found",
        )

    if activity.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity does not belong to user",
        )

    plan = None
    if activity.planned_session_id:
        plan = session.get(PlannedSession, activity.planned_session_id)

    with session.begin():
        activity.planned_session_id = None
        if plan:
            plan.completed_activity_id = None

        _log_decision(
            user_id=user_id,
            activity=activity,
            planned=plan,
            decision="manual_unpair",
            reason="user_action",
            session=session,
        )

        logger.info(
            f"Manually unpaired activity {activity_id} from planned session",
            user_id=user_id,
            planned_session_id=plan.id if plan else None,
        )
