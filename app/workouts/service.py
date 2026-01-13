"""Workout service for persisting workouts to database.

This module provides the service layer for workout persistence.
All workout database operations should go through this service.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.workouts.models import Workout, WorkoutStep
from app.workouts.schemas import WorkoutInputSchema


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
        workout = Workout(
            id=str(uuid.uuid4()),
            user_id=user_id,
            sport=workout_schema.sport,
            source=workout_schema.source,
            source_ref=source_ref,
            total_duration_seconds=workout_schema.total_duration_seconds,
            total_distance_meters=workout_schema.total_distance_meters,
        )
        db.add(workout)
        db.flush()

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
