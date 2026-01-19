"""Orchestration service for manual planned session → structured workout flow.

This is the CORE orchestration layer that follows the CORRECT invariant:
1. Extract attributes (deterministic signal detection)
2. LLM → Structured Workout
3. Create Workout + WorkoutSteps (DB)
4. Return Workout (caller creates PlannedSession with workout_id)

CRITICAL INVARIANT: PlannedSession MUST be created AFTER Workout exists.
This ensures workout_id is NOT NULL at creation time.

This is the ONLY place where LLM is called for manual planned sessions.
"""

from __future__ import annotations

import contextlib

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InternalError, OperationalError, ProgrammingError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import UserSettings
from app.workouts.attribute_extraction import extract_workout_signals
from app.workouts.input import ActivityInput
from app.workouts.llm.step_generator import generate_steps_from_notes
from app.workouts.models import Workout
from app.workouts.workout_factory import WorkoutFactory


def _map_sport_type(session_type: str) -> str:
    """Map PlannedSession type to workout sport type.

    Args:
        session_type: PlannedSession type (Run, Ride, Bike, Swim, Race, etc.)

    Returns:
        Workout sport type (run, ride, swim) - matches database CHECK constraint
    """
    if not session_type:
        return "run"

    activity_lower = session_type.lower()
    sport_map: dict[str, str] = {
        "run": "run",
        "running": "run",
        "race": "run",  # Races are typically running events
        "event": "run",  # Events are typically running events
        "ride": "ride",
        "bike": "ride",
        "cycling": "ride",
        "virtualride": "ride",
        "ebikeride": "ride",
        "swim": "swim",
        "swimming": "swim",
    }
    return sport_map.get(activity_lower, "run")


async def create_structured_workout_from_manual_session(
    session: Session,
    user_id: str,
    _athlete_id: int,
    notes_raw: str,
    session_type: str,
    distance_km: float | None = None,
    duration_minutes: int | None = None,
) -> Workout:
    """Create structured workout from manual session request (orchestration flow).

    This is the ONLY entrypoint for creating structured workouts from manual sessions.
    Follows the CORRECT invariant:
    1. Extract attributes (deterministic signals)
    2. LLM → Structured Workout
    3. WorkoutFactory.create_from_structured_workout()
    4. Return Workout (caller creates PlannedSession with workout_id)

    Args:
        session: Database session (must be in transaction)
        user_id: User ID
        _athlete_id: Athlete ID (unused, kept for API compatibility)
        notes_raw: Raw notes from user input (required)
        session_type: Session type (Run, Ride, Bike, Swim, etc.)
        distance_km: Optional distance in kilometers
        duration_minutes: Optional duration in minutes

    Returns:
        Workout instance with structured steps

    Raises:
        ValueError: If notes_raw is missing or LLM fails
        RuntimeError: If LLM call fails

    Note:
        Commits are handled by the caller. This method only flushes.
        PlannedSession must be created AFTER this returns (with workout_id set).
    """
    # Step 1: Validate notes_raw
    notes_stripped = notes_raw.strip() if notes_raw else ""
    if not notes_stripped:
        raise ValueError("Cannot create structured workout without notes_raw")

    # Step 2: Extract attributes (deterministic signal detection)
    signals = extract_workout_signals(notes_stripped)

    # Map session type to sport
    sport = _map_sport_type(session_type)

    # Prepare distance and duration for both paths
    total_distance_meters = None
    if signals.distance_m:
        total_distance_meters = int(signals.distance_m)
    elif distance_km:
        total_distance_meters = int(distance_km * 1000)

    total_duration_seconds = None
    if signals.duration_s:
        total_duration_seconds = signals.duration_s
    elif duration_minutes:
        total_duration_seconds = int(duration_minutes * 60)

    # For races with minimal notes, create a simple workout structure
    # instead of trying to parse with LLM (which may fail on minimal input)
    is_race = session_type.lower() in {"race", "event"}
    has_minimal_notes = len(notes_stripped.split("\n")) <= 3 and "Distance:" in notes_stripped

    if is_race and has_minimal_notes:
        # Skip LLM parsing for simple race entries
        # Create a basic structured workout directly
        from app.workouts.canonical import StructuredWorkout, WorkoutStep

        # Create a simple single-step workout for the race
        race_step = WorkoutStep(
            order=1,
            name="Race",
            distance_meters=total_distance_meters,
            duration_seconds=None,
            intensity="race",
            target_type=None,
            repeat=1,
            is_recovery=False,
        )

        structured_workout = StructuredWorkout(
            sport=sport,
            total_distance_meters=total_distance_meters,
            total_duration_seconds=None,
            steps=[race_step],
        )

        logger.info(
            "Created simple race workout (skipped LLM parsing)",
            user_id=user_id,
            sport=sport,
            distance_meters=total_distance_meters,
        )
    else:
        # Normal flow: use LLM to parse notes
        activity_input = ActivityInput(
            sport=sport,
            total_distance_meters=total_distance_meters,
            total_duration_seconds=total_duration_seconds,
            notes=notes_stripped,
        )

        # Step 3: LLM → Structured Workout
        logger.info(
            "Generating structured workout from notes",
            user_id=user_id,
            sport=sport,
        )
        structured_workout = await generate_steps_from_notes(activity_input)

    # Step 3.5: Fetch user settings for target calculation
    # Defensive query: handle schema drift (missing ftp_watts column)
    # target_calculation.py already uses getattr() defensively for missing attributes
    # Use savepoint to isolate errors and prevent transaction abortion
    user_settings = None
    savepoint = session.begin_nested()
    try:
        user_settings_result = session.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        ).first()
        user_settings = user_settings_result[0] if user_settings_result else None
        savepoint.commit()
    except ProgrammingError as e:
        try:
            savepoint.rollback()
        except Exception as rollback_error:
            # If savepoint rollback fails, rollback main transaction
            logger.error(
                f"Savepoint rollback failed during ProgrammingError handling: {rollback_error!r}",
                user_id=user_id,
                original_error=str(e),
            )
            session.rollback()
            raise RuntimeError(
                "Database transaction was aborted during user settings query. "
                "The transaction has been rolled back."
            ) from rollback_error
        error_msg = str(e).lower()
        if "ftp_watts" in error_msg or "does not exist" in error_msg:
            logger.warning(
                f"Schema drift detected: user_settings.ftp_watts column missing. "
                f"Continuing without user settings. Run migration to fix: {e!r}",
                user_id=user_id,
            )
            # user_settings remains None, which is handled gracefully downstream
        else:
            # Re-raise if it's a different programming error
            # Savepoint rollback prevents main transaction abortion
            raise
    except (SQLAlchemyError, InternalError, OperationalError) as e:
        # Rollback savepoint to prevent main transaction abortion
        # This allows subsequent operations to continue even if user_settings query fails
        try:
            savepoint.rollback()
        except Exception as rollback_error:
            # If savepoint rollback itself fails, the main transaction may be aborted
            # Rollback the main transaction to ensure clean state
            logger.error(
                f"Savepoint rollback failed - rolling back main transaction: {rollback_error!r}",
                user_id=user_id,
                original_error=str(e),
            )
            session.rollback()
            # Re-raise to prevent continuing with an aborted transaction
            raise RuntimeError(
                "Database transaction was aborted while fetching user settings. "
                "The transaction has been rolled back."
            ) from rollback_error
        logger.warning(
            f"Database error while fetching user settings (savepoint rolled back): {e!r}. "
            f"Continuing without user settings.",
            user_id=user_id,
            error_type=type(e).__name__,
        )
        # Don't re-raise - continue without user_settings to avoid aborting the main transaction
        # user_settings remains None, which is handled gracefully downstream
    except Exception as e:
        # Catch any other non-database errors that won't abort the transaction
        # Rollback savepoint for consistency, even though these errors typically don't abort the transaction
        with contextlib.suppress(Exception):
            savepoint.rollback()
        logger.warning(
            f"Failed to fetch user settings for target calculation: {e!r}. "
            f"Continuing without user settings.",
            user_id=user_id,
        )
        # user_settings remains None, which is handled gracefully downstream

    # Step 4: WorkoutFactory.create_from_structured_workout()
    # NOTE: planned_session_id is None here - it will be set when PlannedSession is created
    workout = WorkoutFactory.create_from_structured_workout(
        session=session,
        structured=structured_workout,
        user_id=user_id,
        source="manual",
        raw_notes=notes_raw,
        planned_session_id=None,  # Will be set when PlannedSession is created
        activity_id=None,
        user_settings=user_settings,
    )

    # HARD GUARD: Ensure workout.id exists before returning
    if workout.id is None:
        raise RuntimeError("Workout must exist before PlannedSession creation")

    logger.info(
        "Created structured workout for manual session",
        workout_id=workout.id,
        user_id=user_id,
        step_count=len(structured_workout.steps),
    )

    return workout
