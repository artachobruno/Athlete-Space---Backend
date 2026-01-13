"""Auto-match service for automatically matching PlannedSessions to Activities.

When reconciliation finds a match, this service:
1. Updates PlannedSession.completed_activity_id
2. Creates a Workout container (status='matched')
3. Does NOT run analysis (that happens later)

This service is idempotent - safe to call multiple times.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.reconciliation import ReconciliationResult, SessionStatus
from app.db.models import PlannedSession
from app.db.session import get_session
from app.workouts.workout_factory import ensure_workout_for_match


def auto_match_sessions(
    user_id: str,
    reconciliation_results: list[ReconciliationResult],
) -> int:
    """Automatically match PlannedSessions to Activities based on reconciliation results.

    For each reconciliation result that indicates a match (COMPLETED or PARTIAL),
    this function:
    1. Updates PlannedSession.completed_activity_id
    2. Creates a Workout with status='matched'
    3. Does NOT run analysis

    This is idempotent - safe to call multiple times with the same results.

    Args:
        user_id: User ID
        reconciliation_results: List of reconciliation results from reconcile_calendar

    Returns:
        Number of sessions matched (workouts created)
    """
    matched_count = 0

    with get_session() as session:
        for result in reconciliation_results:
            # Only process matches (COMPLETED or PARTIAL with matched_activity_id)
            if result.status not in {SessionStatus.COMPLETED, SessionStatus.PARTIAL}:
                continue

            if not result.matched_activity_id:
                continue

            try:
                # Find the planned session
                planned_session = session.execute(
                    select(PlannedSession).where(
                        PlannedSession.id == result.session_id,
                        PlannedSession.user_id == user_id,
                    )
                ).scalar_one_or_none()

                if not planned_session:
                    logger.warning(
                        "Planned session not found for auto-match",
                        session_id=result.session_id,
                        user_id=user_id,
                    )
                    continue

                # Skip if already matched to the same activity (idempotency)
                if planned_session.completed_activity_id == result.matched_activity_id:
                    logger.debug(
                        "Planned session already matched to this activity",
                        session_id=result.session_id,
                        activity_id=result.matched_activity_id,
                    )
                    # Ensure workout exists (in case it was deleted)
                    try:
                        ensure_workout_for_match(
                            user_id=user_id,
                            activity_id=result.matched_activity_id,
                            planned_session_id=result.session_id,
                            db=session,
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to ensure workout for existing match",
                            session_id=result.session_id,
                            activity_id=result.matched_activity_id,
                            error=str(e),
                        )
                    continue

                # Update planned session
                planned_session.completed_activity_id = result.matched_activity_id
                planned_session.completed = True
                planned_session.completed_at = datetime.now(timezone.utc)
                if result.status == SessionStatus.COMPLETED:
                    planned_session.status = "completed"
                elif result.status == SessionStatus.PARTIAL:
                    planned_session.status = "completed"  # Still mark as completed even if partial

                # Create workout (idempotent)
                ensure_workout_for_match(
                    user_id=user_id,
                    activity_id=result.matched_activity_id,
                    planned_session_id=result.session_id,
                    db=session,
                )

                matched_count += 1

                logger.info(
                    "Auto-matched planned session to activity",
                    session_id=result.session_id,
                    activity_id=result.matched_activity_id,
                    status=result.status.value,
                    confidence=result.confidence,
                )

            except Exception as e:
                logger.error(
                    "Failed to auto-match session",
                    session_id=result.session_id,
                    activity_id=result.matched_activity_id,
                    error=str(e),
                )
                # Continue processing other matches
                continue

        # Commit all changes
        session.commit()

    logger.info(
        "Auto-match completed",
        user_id=user_id,
        matched_count=matched_count,
        total_results=len(reconciliation_results),
    )

    return matched_count
