"""Workout factory for creating workouts.

Single source of truth for workout creation. This is the ONLY place
where workouts should be created to enforce the mandatory workout invariant:
- If training exists → a workout exists
- If activity exists → an execution exists
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.workouts.compliance_service import ComplianceService
from app.workouts.execution_models import WorkoutExecution
from app.workouts.models import Workout, WorkoutStep


def _map_sport_type(activity_type: str | None) -> str:
    """Map activity type to workout sport type.

    Args:
        activity_type: Activity type (Run, Ride, Bike, Swim, etc.)

    Returns:
        Workout sport type (run, bike, swim)
    """
    if not activity_type:
        return "run"

    activity_lower = activity_type.lower()
    sport_map: dict[str, str] = {
        "run": "run",
        "running": "run",
        "ride": "bike",
        "bike": "bike",
        "cycling": "bike",
        "virtualride": "bike",
        "swim": "swim",
        "swimming": "swim",
    }
    return sport_map.get(activity_lower, "run")


class WorkoutFactory:
    """Factory for creating workouts. Only place allowed to create workouts."""

    @staticmethod
    def get_or_create_for_planned_session(session: Session, planned_session: PlannedSession) -> Workout:
        """Get or create workout for a planned session.

        If workout_id already exists on the session, returns that workout.
        Otherwise creates a new workout with source='planned' and workout_steps
        from the session, then sets session.workout_id.

        Args:
            session: Database session (must be in transaction)
            planned_session: PlannedSession instance (must be persisted)

        Returns:
            Workout instance

        Note:
            Commits are handled by the caller. This method only flushes.
        """
        # Validate planned_session is a proper ORM object, not a dict
        if not hasattr(planned_session, "id"):
            raise ValueError(f"planned_session must be a PlannedSession instance, got {type(planned_session).__name__}")

        # Check if workout_id already exists
        if planned_session.workout_id:
            existing_workout = session.execute(
                select(Workout).where(Workout.id == planned_session.workout_id)
            ).scalar_one_or_none()
            if existing_workout:
                logger.debug(
                    "Workout already exists for planned session",
                    workout_id=existing_workout.id,
                    planned_session_id=planned_session.id,
                )
                return existing_workout

        # Create workout
        # Safely access attributes using getattr to avoid KeyError
        session_type = getattr(planned_session, "type", None)
        if session_type is None:
            raise ValueError("planned_session.type is required but is None")

        sport = _map_sport_type(session_type)

        duration_minutes = getattr(planned_session, "duration_minutes", None)
        total_duration_seconds = (
            int(duration_minutes * 60) if duration_minutes else None
        )

        distance_km = getattr(planned_session, "distance_km", None)
        total_distance_meters = (
            int(distance_km * 1000) if distance_km else None
        )

        session_id = getattr(planned_session, "id", None)
        if session_id is None:
            raise ValueError("planned_session.id is required but is None")

        user_id = getattr(planned_session, "user_id", None)
        if user_id is None:
            raise ValueError("planned_session.user_id is required but is None")

        workout = Workout(
            user_id=user_id,
            sport=sport,
            source="planned",
            source_ref=None,
            total_duration_seconds=total_duration_seconds,
            total_distance_meters=total_distance_meters,
            planned_session_id=session_id,
            activity_id=None,
        )
        session.add(workout)
        session.flush()

        # Create workout steps from session
        _create_steps_from_planned_session(session, workout, planned_session)

        # Set workout_id on planned_session
        planned_session.workout_id = workout.id

        try:
            workout_id = getattr(workout, "id", None)
            planned_session_id = getattr(planned_session, "id", None)
            user_id_val = getattr(planned_session, "user_id", None)
            logger.info(
                "Created workout for planned session",
                workout_id=workout_id,
                planned_session_id=planned_session_id,
                user_id=user_id_val,
            )
        except Exception:
            # Log error but don't fail the operation
            logger.exception("Failed to log workout creation")

        return workout

    @staticmethod
    def get_or_create_for_activity(session: Session, activity: Activity) -> Workout:
        """Get or create workout for an activity.

        If workout_id already exists on the activity, returns that workout.
        Otherwise creates a new workout with source='inferred' and a single
        main step, then sets activity.workout_id.

        Args:
            session: Database session (must be in transaction)
            activity: Activity instance (must be persisted)

        Returns:
            Workout instance

        Note:
            Commits are handled by the caller. This method only flushes.
        """
        # Check if workout_id already exists
        if activity.workout_id:
            existing_workout = session.execute(
                select(Workout).where(Workout.id == activity.workout_id)
            ).scalar_one_or_none()
            if existing_workout:
                logger.debug(
                    "Workout already exists for activity",
                    workout_id=existing_workout.id,
                    activity_id=activity.id,
                )
                return existing_workout

        # Create workout
        sport = _map_sport_type(activity.type)
        total_duration_seconds = int(activity.duration_seconds) if activity.duration_seconds else None
        total_distance_meters = int(activity.distance_meters) if activity.distance_meters else None

        workout = Workout(
            user_id=activity.user_id,
            sport=sport,
            source="inferred",
            source_ref=None,
            total_duration_seconds=total_duration_seconds,
            total_distance_meters=total_distance_meters,
            activity_id=activity.id,
            planned_session_id=None,
        )
        session.add(workout)
        session.flush()

        # Create single main step
        _create_main_step_from_activity(session, workout, activity)

        # Set workout_id on activity
        activity.workout_id = workout.id

        logger.info(
            "Created workout for activity",
            workout_id=workout.id,
            activity_id=activity.id,
            user_id=activity.user_id,
        )

        return workout

    @staticmethod
    def attach_activity(session: Session, workout: Workout, activity: Activity) -> WorkoutExecution:
        """Attach activity to workout by creating workout execution.

        Creates a WorkoutExecution linking workout to activity.
        Idempotent: if execution already exists, returns it.
        Triggers compliance generation after execution creation.

        Args:
            session: Database session (must be in transaction)
            workout: Workout instance
            activity: Activity instance

        Returns:
            WorkoutExecution instance

        Note:
            Commits are handled by the caller. This method only flushes.
        """
        # Check if execution already exists (idempotency)
        existing_execution = session.execute(
            select(WorkoutExecution).where(
                WorkoutExecution.workout_id == workout.id,
                WorkoutExecution.activity_id == activity.id,
            )
        ).scalar_one_or_none()

        if existing_execution:
            logger.debug(
                "Workout execution already exists",
                execution_id=existing_execution.id,
                workout_id=workout.id,
                activity_id=activity.id,
            )
            return existing_execution

        # Create execution
        execution = WorkoutExecution(
            workout_id=workout.id,
            activity_id=activity.id,
            attached_at=datetime.now(timezone.utc),
        )
        session.add(execution)
        session.flush()

        logger.info(
            "Created workout execution",
            execution_id=execution.id,
            workout_id=workout.id,
            activity_id=activity.id,
        )

        # Trigger compliance generation
        try:
            ComplianceService.compute_and_persist(session, workout.id)
            logger.debug("Generated compliance for workout", extra={"workout_id": workout.id})
        except Exception as e:
            logger.warning(
                "Failed to generate compliance (non-fatal)",
                workout_id=workout.id,
                error=str(e),
            )
            # Don't fail if compliance generation fails

        return execution


def _create_steps_from_planned_session(
    session: Session,
    workout: Workout,
    planned_session: PlannedSession,
) -> None:
    """Create workout steps from planned session data.

    Args:
        session: Database session
        workout: Workout instance
        planned_session: PlannedSession instance
    """
    # Create a single main step representing the entire planned session
    step_duration_seconds = (
        int(planned_session.duration_minutes * 60) if planned_session.duration_minutes else None
    )
    step_distance_meters = (
        int(planned_session.distance_km * 1000) if planned_session.distance_km else None
    )

    # Determine step type based on intensity
    step_type = "steady"
    if planned_session.intensity:
        intensity_lower = planned_session.intensity.lower()
        if intensity_lower in {"easy", "recovery"} or intensity_lower in {"moderate", "tempo", "threshold"}:
            step_type = "steady"
        elif intensity_lower in {"hard", "race", "interval"}:
            step_type = "interval"

    step = WorkoutStep(
        workout_id=workout.id,
        order=0,
        type=step_type,
        duration_seconds=step_duration_seconds,
        distance_meters=step_distance_meters,
        target_metric=None,
        target_min=None,
        target_max=None,
        target_value=None,
        intensity_zone=planned_session.intensity,
        instructions=planned_session.notes,
        purpose=planned_session.title,
        inferred=False,
    )
    session.add(step)


def _create_main_step_from_activity(
    session: Session,
    workout: Workout,
    activity: Activity,
) -> None:
    """Create single main step from activity data.

    Args:
        session: Database session
        workout: Workout instance
        activity: Activity instance
    """
    step_duration_seconds = int(activity.duration_seconds) if activity.duration_seconds else None
    step_distance_meters = int(activity.distance_meters) if activity.distance_meters else None

    step = WorkoutStep(
        workout_id=workout.id,
        order=0,
        type="free",
        duration_seconds=step_duration_seconds,
        distance_meters=step_distance_meters,
        target_metric=None,
        target_min=None,
        target_max=None,
        target_value=None,
        intensity_zone=None,
        instructions=None,
        purpose=f"{activity.type or 'Activity'}",
        inferred=True,
    )
    session.add(step)


# Legacy function for backwards compatibility
def ensure_workout_for_match(
    *,
    user_id: str,
    activity_id: str,
    planned_session_id: str,
    db: Session,
) -> Workout:
    """Idempotently create a Workout for a matched activity + planned session.

    DEPRECATED: Use WorkoutFactory methods instead.
    This function is kept for backwards compatibility.

    Args:
        user_id: User ID
        activity_id: Activity ID
        planned_session_id: Planned session ID
        db: Database session

    Returns:
        Workout instance
    """
    activity = db.execute(select(Activity).where(Activity.id == activity_id)).scalar_one_or_none()
    if not activity:
        raise ValueError(f"Activity {activity_id} not found")
    if activity.user_id != user_id:
        raise ValueError(f"Activity {activity_id} belongs to different user")

    planned_session = db.execute(
        select(PlannedSession).where(PlannedSession.id == planned_session_id)
    ).scalar_one_or_none()
    if not planned_session:
        raise ValueError(f"Planned session {planned_session_id} not found")
    if planned_session.user_id != user_id:
        raise ValueError(f"Planned session {planned_session_id} belongs to different user")

    # Use the planned session's workout (preferred)
    if planned_session.workout_id:
        workout = db.execute(
            select(Workout).where(Workout.id == planned_session.workout_id)
        ).scalar_one_or_none()
        if workout:
            # Attach activity to this workout
            WorkoutFactory.attach_activity(db, workout, activity)
            return workout

    # Fallback: create workout for planned session, then attach activity
    workout = WorkoutFactory.get_or_create_for_planned_session(db, planned_session)
    WorkoutFactory.attach_activity(db, workout, activity)
    return workout
