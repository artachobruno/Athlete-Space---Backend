"""Workout factory for creating workouts from matches.

Single source of truth for workout creation when a PlannedSession
is matched to an Activity. Ensures idempotency and prevents duplicates.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.workouts.models import Workout


def ensure_workout_for_match(
    *,
    user_id: str,
    activity_id: str,
    planned_session_id: str,
    db: Session,
) -> Workout:
    """Idempotently create a Workout for a matched activity + planned session.

    This function ensures that when a PlannedSession is matched to an Activity,
    exactly one Workout is created. It is safe to call multiple times with the
    same parameters - it will return the existing workout if one already exists.

    Rules:
    - Only creates workout if both activity_id and planned_session_id are provided
    - Returns existing workout if one already exists for this pair
    - Sets status to 'matched' (analysis happens later)
    - Does NOT run analysis, stream fetching, or step inference

    Args:
        user_id: User ID
        activity_id: Activity ID (must exist in activities table)
        planned_session_id: Planned session ID (must exist in planned_sessions table)
        db: Database session

    Returns:
        Workout instance (existing or newly created)

    Raises:
        ValueError: If activity or planned session doesn't exist or belongs to different user
    """
    # Validate activity exists and belongs to user
    activity = db.execute(select(Activity).where(Activity.id == activity_id)).scalar_one_or_none()
    if not activity:
        raise ValueError(f"Activity {activity_id} not found")
    if activity.user_id != user_id:
        raise ValueError(f"Activity {activity_id} belongs to different user")

    # Validate planned session exists and belongs to user
    planned_session = db.execute(
        select(PlannedSession).where(PlannedSession.id == planned_session_id)
    ).scalar_one_or_none()
    if not planned_session:
        raise ValueError(f"Planned session {planned_session_id} not found")
    if planned_session.user_id != user_id:
        raise ValueError(f"Planned session {planned_session_id} belongs to different user")

    # Check if workout already exists (idempotency)
    existing = db.execute(
        select(Workout).where(
            Workout.activity_id == activity_id,
            Workout.planned_session_id == planned_session_id,
        )
    ).scalar_one_or_none()

    if existing:
        logger.debug(
            "Workout already exists for match",
            workout_id=existing.id,
            activity_id=activity_id,
            planned_session_id=planned_session_id,
        )
        return existing

    # Determine sport from activity type
    sport_map: dict[str, str] = {
        "run": "run",
        "Run": "run",
        "ride": "bike",
        "Ride": "bike",
        "bike": "bike",
        "Bike": "bike",
        "swim": "swim",
        "Swim": "swim",
    }
    sport = sport_map.get(activity.type or "", "run")  # Default to run

    # Create new workout
    workout = Workout(
        user_id=user_id,
        activity_id=activity_id,
        planned_session_id=planned_session_id,
        sport=sport,
        source="match",
        source_ref=None,
        total_duration_seconds=activity.duration_seconds,
        total_distance_meters=int(activity.distance_meters) if activity.distance_meters else None,
        status="matched",
    )

    db.add(workout)
    db.flush()  # Ensure ID is generated

    logger.info(
        "Created workout for match",
        workout_id=workout.id,
        activity_id=activity_id,
        planned_session_id=planned_session_id,
        user_id=user_id,
    )

    return workout
