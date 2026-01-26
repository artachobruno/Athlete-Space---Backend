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
from app.pairing.delta_computation import compute_link_deltas
from app.pairing.session_links import get_link_for_planned, upsert_link
from app.services.background_feedback_generator import trigger_feedback_generation
from app.services.workout_execution_service import ensure_execution_summary
from app.workouts.execution_models import MatchType, WorkoutExecution
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

                # Schema v2: Skip if already matched to the same activity (idempotency)
                existing_link = get_link_for_planned(session, result.session_id)
                if existing_link and existing_link.activity_id == result.matched_activity_id:
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
                # Schema v2: activity.workout_id does not exist - relationships go through session_links
                # For now, skip inferred workout check (would require querying session_links/executions)
                inferred_workout = None

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
                # Pass planned_session_id and match_type='auto' for auto-matched executions
                WorkoutFactory.attach_activity(
                    session, planned_workout, activity, planned_session_id=planned_session.id, match_type=MatchType.AUTO.value
                )

                # Step 5: Note - activity.workout_id does not exist in schema v2
                # Relationships go through session_links table

                # Step 6: Update workout.activity_id to link workout to activity (CRITICAL FIX)
                planned_workout.activity_id = activity.id

                # Step 7: Update workout distance/duration from actual activity (activity has real values)
                if activity.distance_meters is not None:
                    planned_workout.total_distance_meters = int(activity.distance_meters)
                if activity.duration_seconds is not None:
                    planned_workout.total_duration_seconds = int(activity.duration_seconds)

                # Step 8: Update workout status based on reconciliation result
                if result.status == SessionStatus.COMPLETED:
                    planned_workout.status = "matched"
                elif result.status == SessionStatus.PARTIAL:
                    planned_workout.status = "matched"  # Still matched even if partial

                # PHASE 1.3: Execution outcome is derived via execution_state helper, not stored
                # Do not write execution state to planned_sessions.status
                # Schema v2: Execution state is derived from session_links + time, not stored

                # PHASE 3: Compute deltas when confirming auto-match
                planned_session = session.get(PlannedSession, result.session_id)
                activity = session.get(Activity, result.matched_activity_id)
                deltas = None
                if planned_session and activity:
                    deltas = compute_link_deltas(planned_session, activity)

                # Schema v2: Create/update SessionLink with 'confirmed' status (auto-match is final)
                # Use reconciliation confidence if available, default to 0.9 for auto-match
                confidence_score = result.confidence if hasattr(result, "confidence") and result.confidence else 0.9
                upsert_link(
                    session=session,
                    user_id=user_id,
                    planned_session_id=result.session_id,
                    activity_id=result.matched_activity_id,
                    status="confirmed",  # Auto-match creates confirmed links (final decision)
                    method="auto",
                    confidence=confidence_score,
                    notes=f"Auto-matched via reconciliation: {result.status.value}",
                    deltas=deltas,
                    resolved_at=datetime.now(timezone.utc),
                )

                # PHASE 5.2: Compute and store execution summary
                try:
                    ensure_execution_summary(
                        session=session,
                        planned_session_id=result.session_id,
                        activity_id=result.matched_activity_id,
                        user_id=user_id,
                        force_recompute=True,  # Recompute on confirmation
                    )

                    # PHASE: Trigger LLM feedback generation in background (non-blocking)
                    trigger_feedback_generation(
                        activity_id=result.matched_activity_id,
                        planned_session_id=result.session_id,
                        athlete_level="intermediate",  # TODO: Get from user profile
                    )
                except Exception as e:
                    logger.warning(f"Failed to compute execution summary after auto-match: {e}")
                    # Don't fail auto-match if summary computation fails

                matched_count += 1

                logger.info(
                    "Auto-matched planned session to activity",
                    session_id=result.session_id,
                    activity_id=result.matched_activity_id,
                    planned_workout_id=planned_workout.id,
                    status=result.status.value,
                    confidence=confidence_score,
                )

            except Exception:
                logger.exception(
                    f"Failed to auto-match session (session_id={result.session_id}, activity_id={result.matched_activity_id})"
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
