"""Workout parsing orchestration service.

This module handles idempotent parsing of workout notes into structured steps.
Never blocks session creation - parsing failures are non-fatal.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import delete, select

from app.db.models import UserSettings
from app.db.session import get_session
from app.workouts.canonical import StepIntensity, StepTargetType
from app.workouts.llm_parser import ParsedStep, ParsedWorkout, parse_workout_notes
from app.workouts.models import Workout, WorkoutStep
from app.workouts.target_calculation import calculate_target_from_intensity


def _validate_steps(parsed: ParsedWorkout, workout: Workout, session) -> tuple[bool, list[str]]:
    """Validate parsed steps and collect warnings.

    Args:
        parsed: Parsed workout
        workout: Workout object
        session: Database session

    Returns:
        Tuple of (should_continue, validation_warnings)
    """
    validation_warnings: list[str] = []

    if not parsed.steps:
        workout.parse_status = "failed"
        workout.llm_output_json = {"error": "No steps parsed", "warnings": validation_warnings}
        session.flush()
        return False, validation_warnings

    for step in parsed.steps:
        if step.distance_meters is not None and step.distance_meters < 0:
            validation_warnings.append(f"Step {step.order} has negative distance_meters")
        if step.duration_seconds is not None and step.duration_seconds < 0:
            validation_warnings.append(f"Step {step.order} has negative duration_seconds")

        if step.distance_meters is None and step.duration_seconds is None:
            workout.parse_status = "failed"
            workout.llm_output_json = {
                "error": f"Step {step.order} missing both distance_meters and duration_seconds",
                "warnings": validation_warnings,
            }
            session.flush()
            return False, validation_warnings

    if validation_warnings:
        workout.parse_status = "failed"
        workout.llm_output_json = {
            "error": "Invalid structured output from LLM",
            "warnings": validation_warnings,
        }
        session.flush()
        return False, validation_warnings

    return True, validation_warnings


def _calculate_step_targets(
    parsed_step: ParsedStep,
    user_settings: UserSettings | None,
    workout: Workout,
) -> tuple[str | None, float | None, float | None, float | None]:
    """Calculate target values for a workout step.

    Args:
        parsed_step: Parsed step
        user_settings: User settings for target calculation
        workout: Workout object

    Returns:
        Tuple of (target_metric, target_min, target_max, target_value)
    """
    target_metric: str | None = None
    target_min: float | None = None
    target_max: float | None = None
    target_value: float | None = None

    if parsed_step.target:
        target_type_raw = parsed_step.target.get("type")
        if isinstance(target_type_raw, str):
            target_metric = target_type_raw.lower()
        target_low_raw = parsed_step.target.get("low")
        if isinstance(target_low_raw, (int, float)):
            target_min = float(target_low_raw)
        target_high_raw = parsed_step.target.get("high")
        if isinstance(target_high_raw, (int, float)):
            target_max = float(target_high_raw)

    if not target_metric and user_settings and workout.sport:
        step_type_lower = parsed_step.type.lower() if parsed_step.type else ""
        intensity: StepIntensity | None = None

        if step_type_lower in {"warmup", "cooldown", "rest"}:
            intensity = StepIntensity.EASY
        elif step_type_lower == "steady":
            intensity = StepIntensity.FLOW
        elif step_type_lower == "interval":
            intensity = StepIntensity.THRESHOLD

        if intensity:
            calc_target_type, calc_min, calc_max, calc_value = calculate_target_from_intensity(
                intensity=intensity,
                sport=workout.sport,
                user_settings=user_settings,
            )
            if calc_target_type != StepTargetType.NONE:
                target_metric = calc_target_type.value
                target_min = calc_min
                target_max = calc_max
                target_value = calc_value

    return target_metric, target_min, target_max, target_value


def _persist_workout_steps(
    session,
    workout_id: str,
    parsed: ParsedWorkout,
    workout: Workout,
    user_settings: UserSettings | None,
) -> None:
    """Persist workout steps to database.

    Args:
        session: Database session
        workout_id: Workout ID
        parsed: Parsed workout
        workout: Workout object
        user_settings: User settings for target calculation
    """
    delete_stmt = delete(WorkoutStep).where(WorkoutStep.workout_id == workout_id)
    session.execute(delete_stmt)

    for parsed_step in parsed.steps:
        target_metric, target_min, target_max, target_value = _calculate_step_targets(
            parsed_step, user_settings, workout
        )

        workout_step = WorkoutStep(
            workout_id=workout_id,
            order=parsed_step.order,
            type=parsed_step.type,
            distance_meters=parsed_step.distance_meters,
            duration_seconds=parsed_step.duration_seconds,
            target_metric=target_metric,
            target_min=target_min,
            target_max=target_max,
            target_value=target_value,
        )
        session.add(workout_step)


def validate_parsed_workout(
    parsed: ParsedWorkout,
    total_distance_meters: int | None,
    total_duration_seconds: int | None,
) -> tuple[bool, str | None]:
    """Validate parsed workout against totals.

    Args:
        parsed: Parsed workout
        total_distance_meters: Expected total distance
        total_duration_seconds: Expected total duration
        total_distance_meters: Optional total distance in meters
        total_duration_seconds: Optional total duration in seconds

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not parsed.steps:
        return False, "No steps parsed"

    # Calculate totals from steps
    step_distance_sum = sum(step.distance_meters or 0 for step in parsed.steps)
    step_duration_sum = sum(step.duration_seconds or 0 for step in parsed.steps)

    # Validate distance if provided
    if total_distance_meters:
        tolerance = total_distance_meters * 0.1  # ±10%
        if abs(step_distance_sum - total_distance_meters) > tolerance:
            return False, f"Step distance sum ({step_distance_sum}) doesn't match total ({total_distance_meters}) within ±10%"

    # Validate duration if provided
    if total_duration_seconds:
        tolerance = total_duration_seconds * 0.1  # ±10%
        if abs(step_duration_sum - total_duration_seconds) > tolerance:
            return False, f"Step duration sum ({step_duration_sum}) doesn't match total ({total_duration_seconds}) within ±10%"

    # If neither distance nor duration provided, validation passes
    return True, None


def ensure_workout_steps(workout_id: str) -> None:
    """Ensure workout steps are parsed and persisted.

    This function is idempotent - safe to call multiple times.
    Never raises to caller - failures are logged but don't block.

    Logic:
    1. Load workout
    2. If parse_status == "parsed" → return
    3. Call LLM parser
    4. Validate
    5. Persist steps
    6. Update parse_status and llm_output_json

    Args:
        workout_id: Workout ID to parse
    """
    try:
        with get_session() as session:
            # Load workout
            stmt = select(Workout).where(Workout.id == workout_id)
            workout = session.execute(stmt).scalar_one_or_none()

            if not workout:
                logger.warning(f"Workout {workout_id} not found, skipping parsing")
                return

            # If already parsed, skip
            if workout.parse_status == "parsed":
                logger.debug(f"Workout {workout_id} already parsed, skipping")
                return

            # If no raw_notes, cannot parse
            if not workout.raw_notes or not workout.raw_notes.strip():
                logger.debug(f"Workout {workout_id} has no raw_notes, skipping parsing")
                workout.parse_status = "failed"
                workout.llm_output_json = {"error": "No raw_notes available"}
                session.flush()
                return

            # Parse notes
            try:
                parsed = parse_workout_notes(
                    sport=workout.sport,
                    notes=workout.raw_notes,
                    total_distance_meters=workout.total_distance_meters,
                    total_duration_seconds=workout.total_duration_seconds,
                )
            except Exception as e:
                logger.exception(f"LLM parsing failed for workout {workout_id}")
                workout.parse_status = "failed"
                workout.llm_output_json = {"error": str(e)}
                session.flush()
                return

            # Strict schema validation
            should_continue, _ = _validate_steps(parsed, workout, session)
            if not should_continue:
                return

            # Validate totals
            is_valid, error_msg = validate_parsed_workout(
                parsed,
                workout.total_distance_meters,
                workout.total_duration_seconds,
            )

            if not is_valid:
                logger.warning(f"Parsed workout {workout_id} validation failed: {error_msg}")
                workout.parse_status = "ambiguous"
                workout.llm_output_json = parsed.model_dump()
                session.flush()
                return

            # Fetch user settings for target calculation
            user_settings_result = session.execute(
                select(UserSettings).where(UserSettings.user_id == workout.user_id)
            ).first()
            user_settings = user_settings_result[0] if user_settings_result else None

            # Persist steps (transactional: delete old, insert new)
            _persist_workout_steps(session, workout_id, parsed, workout, user_settings)

            # Check confidence after persisting steps
            # If confidence is low, mark as ambiguous but keep steps
            if parsed.confidence < 0.6:
                logger.warning(
                    f"Parsed workout {workout_id} has low confidence ({parsed.confidence}), marking as ambiguous"
                )
                workout.parse_status = "ambiguous"
            else:
                workout.parse_status = "parsed"

            workout.llm_output_json = parsed.model_dump()

            session.flush()

            logger.info(
                "Workout steps parsed and persisted",
                workout_id=workout_id,
                step_count=len(parsed.steps),
                confidence=parsed.confidence,
            )

    except Exception:
        # Never raise to caller - log and continue
        logger.exception(f"Error parsing workout {workout_id}")
