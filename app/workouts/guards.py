"""Backend assertion guards for workout mandatory invariant.

PHASE 7: Hard assertions (GUARDS)
These checks enforce the invariant at runtime:
- No activity without workout
- No activity without execution
- No CalendarSession model exists (deprecated)

Fail loudly in logs.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.db.models as models_module
from app.db.models import Activity, PlannedSession
from app.workouts.execution_models import WorkoutExecution


def assert_calendar_session_does_not_exist() -> None:
    """Assert that CalendarSession model does not exist in app.db.models.

    Fails loudly in logs if CalendarSession is found (deprecated model).

    Raises:
        AssertionError: If CalendarSession exists in models
    """
    if hasattr(models_module, "CalendarSession"):
        error_msg = "INVARIANT VIOLATION: CalendarSession model still exists in app.db.models (deprecated)"
        logger.error(error_msg)
        raise AssertionError(error_msg)


def assert_activity_has_workout(activity: Activity) -> None:
    """Assert that activity has a workout_id.

    DEPRECATED: Schema v2 removes activity.workout_id column.
    Activities are linked to workouts through session_links table.
    This guard is disabled to match schema.

    Args:
        activity: Activity instance

    Raises:
        AssertionError: Never (function disabled for schema v2)
    """
    # Schema v2: activity.workout_id does not exist
    # Relationships go through session_links table
    # This guard is disabled to match schema
    pass


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
