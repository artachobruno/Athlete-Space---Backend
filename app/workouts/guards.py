"""Backend assertion guards for workout mandatory invariant.

PHASE 7: Hard assertions (GUARDS)
These checks enforce the invariant at runtime:
- No activity without workout
- No activity without execution

Fail loudly in logs.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.workouts.execution_models import WorkoutExecution


def assert_activity_has_workout(activity: Activity) -> None:
    """Assert that activity has a workout_id.

    Fails loudly in logs if invariant is violated.

    Args:
        activity: Activity instance

    Raises:
        AssertionError: If activity.workout_id is None
    """
    if activity.workout_id is None:
        error_msg = f"INVARIANT VIOLATION: Activity {activity.id} has no workout_id"
        logger.error(error_msg, activity_id=activity.id, user_id=activity.user_id)
        raise AssertionError(error_msg)


def assert_activity_has_execution(session: Session, activity: Activity) -> None:
    """Assert that activity has a workout execution.

    Fails loudly in logs if invariant is violated.

    Args:
        session: Database session
        activity: Activity instance

    Raises:
        AssertionError: If no execution exists for activity
    """
    execution = session.execute(
        select(WorkoutExecution).where(WorkoutExecution.activity_id == activity.id)
    ).scalar_one_or_none()

    if execution is None:
        error_msg = f"INVARIANT VIOLATION: Activity {activity.id} has no workout execution"
        logger.error(
            error_msg,
            activity_id=activity.id,
            user_id=activity.user_id,
            workout_id=activity.workout_id,
        )
        raise AssertionError(error_msg)


def assert_planned_session_has_workout(planned_session: PlannedSession) -> None:
    """Assert that planned session has a workout_id.

    Fails loudly in logs if invariant is violated.

    Args:
        planned_session: PlannedSession instance

    Raises:
        AssertionError: If planned_session.workout_id is None
    """
    if planned_session.workout_id is None:
        error_msg = f"INVARIANT VIOLATION: PlannedSession {planned_session.id} has no workout_id"
        logger.error(
            error_msg,
            session_id=planned_session.id,
            user_id=planned_session.user_id,
        )
        raise AssertionError(error_msg)
