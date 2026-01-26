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

from datetime import timezone

from app.db.models import Activity, PairingDecision, PlannedSession
from app.pairing.delta_computation import compute_link_deltas
from app.pairing.session_links import get_link_for_activity, unlink_by_activity, upsert_link
from app.plans.reconciliation.service import reconcile_activity_if_paired
from app.workouts.execution_models import MatchType
from app.workouts.workout_factory import WorkoutFactory

# DB pairing_decisions.decision CHECK: accept | reject | manual_link | manual_unlink
_DECISION_TO_DB: dict[str, str] = {
    "manual_pair": "manual_link",
    "manual_unpair": "manual_unlink",
}


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

    Uses DB-allowed decision values (manual_link, manual_unlink).

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
    decision_db = _DECISION_TO_DB.get(decision, decision)

    pairing_decision = PairingDecision(
        user_id=user_id,
        planned_session_id=planned_session_id,
        activity_id=activity_id,
        decision=decision_db,
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
        # PHASE 3: Compute deltas when confirming manual pairing
        deltas = compute_link_deltas(plan, activity)
        
        # Schema v2: Use SessionLink for pairing
        # upsert_link handles clearing existing links automatically
        upsert_link(
            session=session,
            user_id=user_id,
            planned_session_id=planned_session_id,
            activity_id=activity_id,
            status="confirmed",
            method="manual",
            confidence=1.0,
            notes="Manually paired by user",
            match_reason={"manual": True, "user_action": True},
            deltas=deltas,
            resolved_at=datetime.now(timezone.utc),
        )

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

        # Ensure workout exists for planned session
        try:
            workout = WorkoutFactory.get_or_create_for_planned_session(session, plan)
            logger.debug(
                f"Workout ensured for planned session {plan.id}",
                workout_id=workout.id,
            )
        except Exception as e:
            logger.warning(
                f"Failed to get/create workout for planned session {plan.id}: {e}",
            )
            # Continue even if workout creation fails - pairing still succeeds
            workout = None

        # Note: activity.workout_id does not exist in schema v2
        # Relationships go through session_links table (planned_sessions <-> session_links <-> activities)
        if workout:
            # Create WorkoutExecution (triggers compliance calculation)
            # Pass match_type='manual' for manually paired executions
            try:
                WorkoutFactory.attach_activity(
                    session,
                    workout,
                    activity,
                    planned_session_id=planned_session_id,
                    match_type=MatchType.MANUAL.value,
                )
                logger.debug(
                    f"Created execution and compliance for workout {workout.id}",
                )
            except Exception as e:
                logger.warning(
                    f"Failed to create execution/compliance for workout {workout.id}: {e}",
                )
                # Continue even if execution/compliance creation fails

        # Perform HR-based reconciliation (passive, read-only)
        try:
            reconcile_activity_if_paired(session, activity)
        except Exception as e:
            logger.warning(f"Reconciliation failed after manual pairing {activity_id} with {planned_session_id}: {e}")


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

    # Schema v2: Get plan from SessionLink before unlinking
    link = get_link_for_activity(session, activity_id)
    plan = None
    if link:
        plan = session.get(PlannedSession, link.planned_session_id)

    with session.begin():
        # Schema v2: Use SessionLink for unpairing
        unlink_by_activity(session, activity_id, reason="Manual unpair by user")

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
