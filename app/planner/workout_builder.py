"""Workout builder for converting planner output to canonical workout format.

This module provides pure transformation functions that convert planner output
into WorkoutSchema format. No DB operations. No side effects.

Integration Example:
    When the planner finalizes a workout, use this pattern:

    ```python
    from app.planner.workout_builder import (
        adapt_planned_session_to_plan,
        build_workout_from_plan,
    )
    from app.workouts.service import WorkoutService
    from app.db.session import get_session

    # After planner creates a PlannedSession with text_output
    planned_session = ...  # From planner
    plan_id = ...  # Plan identifier

    # Convert to Plan interface
    plan = adapt_planned_session_to_plan(
        planned_session=planned_session,
        sport="run",
        plan_id=plan_id,
    )

    # Build workout schema
    workout_schema = build_workout_from_plan(plan)

    # Persist to database
    with get_session() as db:
        workout = WorkoutService.save_workout(
            db=db,
            user_id=user_id,
            workout_schema=workout_schema,
            source_ref=plan_id,
        )
        db.commit()
    ```
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.planner.models import PlannedSession
from app.workouts.models import Workout
from app.workouts.schemas import WorkoutInputSchema, WorkoutStepInputSchema
from app.workouts.service import WorkoutService


@dataclass
class PlanSession:
    """Simplified session representation from planner.

    This is a minimal interface that the builder expects.
    The actual planner output should be adapted to this interface.
    """

    type: str
    duration_seconds: int | None = None
    distance_meters: int | None = None
    target_metric: str | None = None
    target_min: float | None = None
    target_max: float | None = None
    target_value: float | None = None
    instructions: str | None = None
    purpose: str | None = None


@dataclass
class Plan:
    """Simplified plan representation from planner.

    This is a minimal interface that the builder expects.
    The actual planner output should be adapted to this interface.
    """

    sport: str
    sessions: list[PlanSession]
    total_duration_seconds: int | None = None
    total_distance_meters: int | None = None
    plan_id: str | None = None


def build_workout_from_plan(plan: Plan) -> WorkoutInputSchema:
    """Convert planner output into canonical Workout + Steps.

    This is a pure transformation function. NO DB. NO SIDE EFFECTS.

    Args:
        plan: Plan object with sessions

    Returns:
        WorkoutInputSchema with ordered steps

    Rules:
    - Order must be deterministic (enumerate sessions in order)
    - Every session becomes exactly one step
    - No collapsing or inference
    """
    steps: list[WorkoutStepInputSchema] = []

    for idx, session in enumerate(plan.sessions, start=1):
        steps.append(
            WorkoutStepInputSchema(
                order=idx,
                type=session.type,
                duration_seconds=session.duration_seconds,
                distance_meters=session.distance_meters,
                target_metric=session.target_metric,
                target_min=session.target_min,
                target_max=session.target_max,
                target_value=session.target_value,
                instructions=session.instructions,
                purpose=session.purpose,
                inferred=False,
            )
        )

    return WorkoutInputSchema(
        sport=plan.sport,
        source="planner",
        total_duration_seconds=plan.total_duration_seconds,
        total_distance_meters=plan.total_distance_meters,
        steps=steps,
    )


def adapt_planned_session_to_plan(
    planned_session: PlannedSession,
    sport: str = "run",
    plan_id: str | None = None,
) -> Plan:
    """Adapt a PlannedSession from the planner to the Plan interface.

    Converts a single PlannedSession into a Plan with one session.
    Extracts data from PlannedSession and SessionTextOutput.

    Args:
        planned_session: PlannedSession from planner
        sport: Sport type (default: "run")
        plan_id: Optional plan ID

    Returns:
        Plan object with one session
    """
    text_output = planned_session.text_output

    # Extract duration from computed metrics or template
    duration_seconds: int | None = None
    if text_output and "total_duration_min" in text_output.computed:
        dur_min = text_output.computed["total_duration_min"]
        if isinstance(dur_min, (int, float)):
            duration_seconds = int(dur_min * 60)

    # Extract distance from computed metrics or session distance
    distance_meters: int | None = None
    if text_output and "total_distance_mi" in text_output.computed:
        dist_mi = text_output.computed["total_distance_mi"]
        if isinstance(dist_mi, (int, float)):
            # Convert miles to meters
            distance_meters = int(float(dist_mi) * 1609.34)
    elif planned_session.distance > 0:
        # Assume distance is in miles, convert to meters
        distance_meters = int(planned_session.distance * 1609.34)

    # Extract type from day_type
    session_type = planned_session.day_type.value if hasattr(planned_session.day_type, "value") else str(planned_session.day_type)

    # Extract instructions and purpose from text_output
    instructions = text_output.description if text_output else None
    purpose = text_output.title if text_output else None

    # For now, we don't extract target metrics from PlannedSession
    # This would need to be extracted from template params or structure
    target_metric = None
    target_min = None
    target_max = None
    target_value = None

    session = PlanSession(
        type=session_type,
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
        target_metric=target_metric,
        target_min=target_min,
        target_max=target_max,
        target_value=target_value,
        instructions=instructions,
        purpose=purpose,
    )

    return Plan(
        sport=sport,
        sessions=[session],
        total_duration_seconds=duration_seconds,
        total_distance_meters=distance_meters,
        plan_id=plan_id,
    )


def adapt_planned_sessions_to_plan(
    planned_sessions: list[PlannedSession],
    sport: str = "run",
    plan_id: str | None = None,
) -> Plan:
    """Adapt multiple PlannedSessions from the planner to the Plan interface.

    Converts multiple PlannedSessions into a Plan with multiple sessions.
    Each PlannedSession becomes one step in the workout.

    Args:
        planned_sessions: List of PlannedSession from planner
        sport: Sport type (default: "run")
        plan_id: Optional plan ID

    Returns:
        Plan object with multiple sessions
    """
    sessions: list[PlanSession] = []
    total_duration_seconds = 0
    total_distance_meters = 0

    for planned_session in planned_sessions:
        text_output = planned_session.text_output

        # Extract duration from computed metrics or template
        duration_seconds: int | None = None
        if text_output and "total_duration_min" in text_output.computed:
            dur_min = text_output.computed["total_duration_min"]
            if isinstance(dur_min, (int, float)):
                duration_seconds = int(dur_min * 60)
                total_duration_seconds += duration_seconds

        # Extract distance from computed metrics or session distance
        distance_meters: int | None = None
        if text_output and "total_distance_mi" in text_output.computed:
            dist_mi = text_output.computed["total_distance_mi"]
            if isinstance(dist_mi, (int, float)):
                # Convert miles to meters
                distance_meters = int(float(dist_mi) * 1609.34)
                total_distance_meters += distance_meters
        elif planned_session.distance > 0:
            # Assume distance is in miles, convert to meters
            distance_meters = int(planned_session.distance * 1609.34)
            total_distance_meters += distance_meters

        # Extract type from day_type
        session_type = planned_session.day_type.value if hasattr(planned_session.day_type, "value") else str(planned_session.day_type)

        # Extract instructions and purpose from text_output
        instructions = text_output.description if text_output else None
        purpose = text_output.title if text_output else None

        # For now, we don't extract target metrics from PlannedSession
        # This would need to be extracted from template params or structure
        target_metric = None
        target_min = None
        target_max = None
        target_value = None

        sessions.append(
            PlanSession(
                type=session_type,
                duration_seconds=duration_seconds,
                distance_meters=distance_meters,
                target_metric=target_metric,
                target_min=target_min,
                target_max=target_max,
                target_value=target_value,
                instructions=instructions,
                purpose=purpose,
            )
        )

    return Plan(
        sport=sport,
        sessions=sessions,
        total_duration_seconds=total_duration_seconds if total_duration_seconds > 0 else None,
        total_distance_meters=total_distance_meters if total_distance_meters > 0 else None,
        plan_id=plan_id,
    )


# Integration helper - call this from planner after workout is finalized
def persist_workout_from_planned_session(
    planned_session: PlannedSession,
    db: Session,
    user_id: str,
    sport: str = "run",
    plan_id: str | None = None,
) -> Workout:
    """Helper function to persist a PlannedSession as a Workout.

    This function handles the full workflow:
    1. Adapt PlannedSession to Plan interface
    2. Build WorkoutInputSchema
    3. Save to database via WorkoutService

    Call this from the planner after a workout is finalized (has text_output).

    Args:
        planned_session: PlannedSession with text_output set
        db: Database session
        user_id: User ID
        sport: Sport type (default: "run")
        plan_id: Optional plan ID for source_ref

    Returns:
        Created Workout model instance

    Example:
        ```python
        from app.planner.workout_builder import persist_workout_from_planned_session
        from app.db.session import get_session

        # After planner creates PlannedSession with text_output
        planned_session = ...  # From planner with text_output set
        plan_id = ...  # Plan identifier

        with get_session() as db:
            workout = persist_workout_from_planned_session(
                planned_session=planned_session,
                db=db,
                user_id=user_id,
                sport="run",
                plan_id=plan_id,
            )
            db.commit()
        ```
    """
    # Adapt to Plan interface
    plan = adapt_planned_session_to_plan(
        planned_session=planned_session,
        sport=sport,
        plan_id=plan_id,
    )

    # Build workout schema
    workout_schema = build_workout_from_plan(plan)

    # Persist via service
    return WorkoutService.save_workout(
        db=db,
        user_id=user_id,
        workout_schema=workout_schema,
        source_ref=plan_id,
    )
