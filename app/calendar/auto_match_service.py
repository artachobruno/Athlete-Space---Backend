"""Auto-match service for automatically matching PlannedSessions to Activities.

When reconciliation finds a match, this service:
1. Updates PlannedSession.completed_activity_id
2. Merges workouts (planned workout beats inferred)
3. Creates workout execution and compliance

This service is idempotent - safe to call multiple times.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calendar.reconciliation import ReconciliationResult, SessionStatus
from app.db.models import Activity, PlannedSession
from app.db.session import get_session
from app.workouts.execution_models import WorkoutExecution
from app.workouts.models import Workout, WorkoutStep
from app.workouts.workout_factory import WorkoutFactory


def auto_match_sessions(
    user_id: str,
    reconciliation_results: list[ReconciliationResult],
) -> int:
    """Automatically match PlannedSessions to Activities based on reconciliation results.

    PHASE 4: Merge workouts on match.
    For each reconciliation result that indicates a match (COMPLETED or PARTIAL),
    this function:
    1. Gets planned workout (from planned_session.workout_id)
    2. Attaches activity to planned workout
    3. If activity had inferred workout:
       - Repoints execution to planned workout
       - Deletes inferred workout
    4. Updates PlannedSession.completed_activity_id

    This is idempotent - safe to call multiple times with the same results.

    Args:
        user_id: User ID
        reconciliation_results: List of reconciliation results from reconcile_calendar

    Returns:
        Number of sessions matched
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

                # Find the activity
                activity = session.execute(
                    select(Activity).where(
                        Activity.id == result.matched_activity_id,
                        Activity.user_id == user_id,
                    )
                ).scalar_one_or_none()

                if not activity:
                    logger.warning(
                        "Activity not found for auto-match",
                        activity_id=result.matched_activity_id,
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
                    continue

                # PHASE 4: Merge workouts - planned beats inferred
                # Step 1: Get or create planned workout
                planned_workout = WorkoutFactory.get_or_create_for_planned_session(session, planned_session)
                session.flush()

                # Step 2: Check if activity has inferred workout
                inferred_workout_id = activity.workout_id
                inferred_workout = None
                if inferred_workout_id and inferred_workout_id != planned_workout.id:
                    inferred_workout = session.execute(
                        select(Workout).where(
                            Workout.id == inferred_workout_id,
                            Workout.source == "inferred",
                        )
                    ).scalar_one_or_none()

                # Step 3: If activity has inferred workout, repoint execution and delete inferred
                if inferred_workout:
                    # Find execution pointing to inferred workout
                    execution = session.execute(
                        select(WorkoutExecution).where(
                            WorkoutExecution.workout_id == inferred_workout.id,
                            WorkoutExecution.activity_id == activity.id,
                        )
                    ).scalar_one_or_none()

                    if execution:
                        # Repoint execution to planned workout
                        execution.workout_id = planned_workout.id
                        logger.info(
                            "Repointed execution from inferred to planned workout",
                            execution_id=execution.id,
                            old_workout_id=inferred_workout.id,
                            new_workout_id=planned_workout.id,
                            activity_id=activity.id,
                        )

                    # Delete inferred workout (cascade will delete steps)
                    session.delete(inferred_workout)
                    logger.info(
                        "Deleted inferred workout after merge",
                        inferred_workout_id=inferred_workout.id,
                        planned_workout_id=planned_workout.id,
                    )

                # Step 4: Attach activity to planned workout (creates execution if needed)
                WorkoutFactory.attach_activity(session, planned_workout, activity)

                # Step 5: Update activity.workout_id to point to planned workout
                activity.workout_id = planned_workout.id

                # Update planned session
                planned_session.completed_activity_id = result.matched_activity_id
                planned_session.completed = True
                planned_session.completed_at = datetime.now(timezone.utc)
                if result.status == SessionStatus.COMPLETED:
                    planned_session.status = "completed"
                elif result.status == SessionStatus.PARTIAL:
                    planned_session.status = "completed"  # Still mark as completed even if partial

                matched_count += 1

                logger.info(
                    "Auto-matched planned session to activity",
                    session_id=result.session_id,
                    activity_id=result.matched_activity_id,
                    planned_workout_id=planned_workout.id,
                    status=result.status.value,
                    confidence=result.confidence,
                )

            except Exception as e:
                logger.error(
                    "Failed to auto-match session",
                    session_id=result.session_id,
                    activity_id=result.matched_activity_id,
                    error=str(e),
                    exc_info=True,
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
