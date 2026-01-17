"""Workout service for persisting workouts to database.

This module provides the service layer for workout persistence.
All workout database operations should go through this service.
"""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.db.session import get_session
from app.workouts.execution_models import WorkoutExecution
from app.workouts.models import Workout, WorkoutStep
from app.workouts.parsing_service import ensure_workout_steps
from app.workouts.schemas import WorkoutInputSchema


def ensure_workout(
    *,
    user_id: str,
    planned_session_id: str | None = None,
    activity_id: str | None = None,
) -> Workout:
    """Create or fetch workout by planned_session_id or activity_id.

    This is the SINGLE workout creation path. All workout creation
    must go through this function.

    Rules:
    - If workout exists (by planned_session_id or activity_id) → return it
    - Else → create it with required fields
    - Populate raw_notes from planned session notes (preferred) or activity description (fallback)
    - Set parse_status = "pending"

    Args:
        user_id: User ID
        planned_session_id: Optional planned session ID
        activity_id: Optional activity ID

    Returns:
        Workout model instance

    Raises:
        ValueError: If neither planned_session_id nor activity_id is provided
        ValueError: If planned_session_id or activity_id doesn't exist or doesn't belong to user
    """
    if not planned_session_id and not activity_id:
        raise ValueError("Either planned_session_id or activity_id must be provided")

    with get_session() as session:
        # Check if workout already exists through workout_executions
        existing_workout: Workout | None = None
        if planned_session_id:
            # Check if planned session has a workout
            planned_stmt = select(PlannedSession).where(
                PlannedSession.id == planned_session_id,
                PlannedSession.user_id == user_id,
            )
            planned_session = session.execute(planned_stmt).scalar_one_or_none()
            if planned_session and planned_session.workout_id:
                existing_workout = session.execute(
                    select(Workout).where(Workout.id == planned_session.workout_id)
                ).scalar_one_or_none()
        elif activity_id:
            # Check if activity has a workout execution
            execution = session.execute(
                select(WorkoutExecution).where(WorkoutExecution.activity_id == activity_id).limit(1)
            ).scalar_one_or_none()
            if execution:
                existing_workout = session.execute(
                    select(Workout).where(Workout.id == execution.workout_id)
                ).scalar_one_or_none()

        if existing_workout:
            logger.debug(
                "Workout already exists",
                workout_id=existing_workout.id,
                planned_session_id=planned_session_id,
                activity_id=activity_id,
            )
            return existing_workout

        # Create new workout
        # Fetch source data to populate fields
        sport = "run"
        total_distance_meters: int | None = None
        total_duration_seconds: int | None = None
        raw_notes: str | None = None
        source = "planned"
        workout_name = "Workout"
        description: str | None = None

        if planned_session_id:
            # Fetch planned session
            planned_stmt = select(PlannedSession).where(
                PlannedSession.id == planned_session_id,
                PlannedSession.user_id == user_id,
            )
            planned_session = session.execute(planned_stmt).scalar_one_or_none()
            if not planned_session:
                raise ValueError(f"Planned session {planned_session_id} not found or doesn't belong to user")

            # Map activity type to sport
            activity_type_lower = (planned_session.type or "").lower()
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
            sport = sport_map.get(activity_type_lower, "run")

            # Get distance and duration
            if planned_session.distance_km:
                total_distance_meters = int(planned_session.distance_km * 1000)
            if planned_session.duration_minutes:
                total_duration_seconds = int(planned_session.duration_minutes * 60)

            # Get raw_notes and description from planned session
            raw_notes = planned_session.notes
            description = planned_session.notes
            workout_name = planned_session.title if planned_session.title else f"{sport.capitalize()} Workout"

            source = "planned"

        elif activity_id:
            # Fetch activity
            activity_stmt = select(Activity).where(
                Activity.id == activity_id,
                Activity.user_id == user_id,
            )
            activity = session.execute(activity_stmt).scalar_one_or_none()
            if not activity:
                raise ValueError(f"Activity {activity_id} not found or doesn't belong to user")

            # Map activity type to sport
            activity_type_lower = (activity.type or "").lower()
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
            sport = sport_map.get(activity_type_lower, "run")

            # Get distance and duration
            if activity.distance_meters:
                total_distance_meters = int(activity.distance_meters)
            if activity.duration_seconds:
                total_duration_seconds = activity.duration_seconds

            # Get raw_notes and description from activity
            activity_title = getattr(activity, "title", None)
            workout_name = activity_title if activity_title else f"{sport.capitalize()} Activity"
            if activity.raw_json and isinstance(activity.raw_json, dict):
                raw_notes = activity.raw_json.get("description") or activity.raw_json.get("name")
                description = raw_notes

            source = "inferred"

        # Create workout template
        workout = Workout(
            id=str(uuid.uuid4()),
            user_id=user_id,
            sport=sport,
            name=workout_name,
            description=description,
            structure={},
            tags={},
            source=source,
            source_ref=None,
            raw_notes=raw_notes,
            parse_status="pending",
        )
        session.add(workout)
        session.flush()

        # Create workout execution if activity_id is provided
        if activity_id:
            execution = WorkoutExecution(
                user_id=user_id,
                workout_id=workout.id,
                activity_id=activity_id,
                planned_session_id=planned_session_id,
                duration_seconds=total_duration_seconds,
                distance_meters=total_distance_meters,
                status="matched",
            )
            session.add(execution)
            session.flush()

        logger.info(
            "Workout created",
            workout_id=workout.id,
            user_id=user_id,
            planned_session_id=planned_session_id,
            activity_id=activity_id,
            sport=sport,
        )

        # Trigger parsing immediately after workout creation (non-blocking)
        try:
            ensure_workout_steps(workout.id)
        except Exception as e:
            logger.warning(
                "Workout parsing failed (non-blocking)",
                workout_id=workout.id,
                error=str(e),
            )

        return workout


class WorkoutService:
    """Service for workout persistence operations."""

    @staticmethod
    def save_workout(
        db: Session,
        user_id: str,
        workout_schema: WorkoutInputSchema,
        source_ref: str | None = None,
    ) -> Workout:
        """Save a workout with steps to the database.

        Args:
            db: Database session
            user_id: User ID
            workout_schema: Workout input schema with steps
            source_ref: Optional reference to source system (e.g., plan_id)

        Returns:
            Created Workout model instance
        """
        # Create workout template
        workout_name = f"{workout_schema.sport.capitalize()} Workout"
        workout = Workout(
            id=str(uuid.uuid4()),
            user_id=user_id,
            sport=workout_schema.sport,
            name=workout_name,
            description=None,
            structure={},
            tags={},
            source=workout_schema.source,
            source_ref=source_ref,
            raw_notes=None,
            parse_status=None,
        )
        db.add(workout)
        db.flush()

        # Note: WorkoutExecution should be created separately if activity_id is known

        for step_schema in workout_schema.steps:
            db.add(
                WorkoutStep(
                    id=str(uuid.uuid4()),
                    workout_id=workout.id,
                    order=step_schema.order,
                    type=step_schema.type,
                    duration_seconds=step_schema.duration_seconds,
                    distance_meters=step_schema.distance_meters,
                    target_metric=step_schema.target_metric,
                    target_min=step_schema.target_min,
                    target_max=step_schema.target_max,
                    target_value=step_schema.target_value,
                    instructions=step_schema.instructions,
                    purpose=step_schema.purpose,
                    inferred=step_schema.inferred,
                )
            )

        return workout
