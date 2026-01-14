"""Orchestration service for manual planned session → structured workout flow.

This is the CORE orchestration layer that follows the spec flow:
1. PlannedSession already exists (with notes_raw)
2. Extract attributes (deterministic signal detection)
3. LLM → Structured Workout
4. WorkoutFactory.create_from_structured_workout()
5. Attach workout_id to planned_session
6. Persist

This is the ONLY place where LLM is called for manual planned sessions.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy.orm import Session

from app.db.models import PlannedSession
from app.workouts.attribute_extraction import extract_workout_signals
from app.workouts.input import ActivityInput
from app.workouts.llm.step_generator import generate_steps_from_notes
from app.workouts.models import Workout
from app.workouts.workout_factory import WorkoutFactory


def _map_sport_type(session_type: str) -> str:
    """Map PlannedSession type to workout sport type.

    Args:
        session_type: PlannedSession type (Run, Ride, Bike, Swim, etc.)

    Returns:
        Workout sport type (run, bike, swim)
    """
    if not session_type:
        return "run"

    activity_lower = session_type.lower()
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


async def create_structured_workout_from_manual_session(
    session: Session,
    planned_session: PlannedSession,
) -> Workout:
    """Create structured workout from manual planned session (orchestration flow).

    This is the ONLY entrypoint for creating structured workouts from manual sessions.
    Follows the spec flow:
    1. Read planned_session.notes_raw
    2. Extract attributes (deterministic signals)
    3. LLM → Structured Workout
    4. WorkoutFactory.create_from_structured_workout()
    5. Attach workout_id to planned_session
    6. Persist (caller commits)

    Args:
        session: Database session (must be in transaction)
        planned_session: PlannedSession instance (must have notes_raw)

    Returns:
        Workout instance with structured steps

    Raises:
        ValueError: If notes_raw is missing or LLM fails
        RuntimeError: If LLM call fails

    Note:
        Commits are handled by the caller. This method only flushes.
    """
    # Step 1: Read planned_session.notes_raw
    notes_raw = planned_session.notes_raw
    if not notes_raw or not notes_raw.strip():
        raise ValueError("Cannot create structured workout without notes_raw")

    # Step 2: Extract attributes (deterministic signal detection)
    signals = extract_workout_signals(notes_raw)

    # Map session type to sport
    sport = _map_sport_type(planned_session.type)

    # Prepare ActivityInput for LLM
    # Use extracted signals if available, otherwise use planned_session fields
    total_distance_meters = None
    if signals.distance_m:
        total_distance_meters = int(signals.distance_m)
    elif planned_session.distance_km:
        total_distance_meters = int(planned_session.distance_km * 1000)

    total_duration_seconds = None
    if signals.duration_s:
        total_duration_seconds = signals.duration_s
    elif planned_session.duration_minutes:
        total_duration_seconds = int(planned_session.duration_minutes * 60)

    activity_input = ActivityInput(
        sport=sport,
        total_distance_meters=total_distance_meters,
        total_duration_seconds=total_duration_seconds,
        notes=notes_raw,
    )

    # Step 3: LLM → Structured Workout
    logger.info(
        "Generating structured workout from notes",
        planned_session_id=planned_session.id,
        sport=sport,
    )
    structured_workout = await generate_steps_from_notes(activity_input)

    # Step 4: WorkoutFactory.create_from_structured_workout()
    workout = WorkoutFactory.create_from_structured_workout(
        session=session,
        structured=structured_workout,
        user_id=planned_session.user_id,
        source="manual",
        raw_notes=notes_raw,
        planned_session_id=planned_session.id,
        activity_id=None,
    )

    # Step 5: Attach workout_id to planned_session
    planned_session.workout_id = workout.id

    logger.info(
        "Created structured workout for manual planned session",
        workout_id=workout.id,
        planned_session_id=planned_session.id,
        step_count=len(structured_workout.steps),
    )

    return workout
