"""Workout parsing orchestration service.

This module handles idempotent parsing of workout notes into structured steps.
Never blocks session creation - parsing failures are non-fatal.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import delete, select

from app.db.session import get_session
from app.workouts.llm_parser import ParsedStep, ParsedWorkout, parse_workout_notes
from app.workouts.models import Workout, WorkoutStep


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
            validation_warnings: list[str] = []

            # Check for required fields and negative values
            if not parsed.steps:
                workout.parse_status = "failed"
                workout.llm_output_json = {"error": "No steps parsed", "warnings": validation_warnings}
                session.flush()
                return

            for step in parsed.steps:
                # Check for negative values
                if step.distance_meters is not None and step.distance_meters < 0:
                    validation_warnings.append(f"Step {step.order} has negative distance_meters")
                if step.duration_seconds is not None and step.duration_seconds < 0:
                    validation_warnings.append(f"Step {step.order} has negative duration_seconds")

                # Check that step has either distance or duration
                if step.distance_meters is None and step.duration_seconds is None:
                    workout.parse_status = "failed"
                    workout.llm_output_json = {
                        "error": f"Step {step.order} missing both distance_meters and duration_seconds",
                        "warnings": validation_warnings,
                    }
                    session.flush()
                    return

            # Reject if validation warnings indicate invalid output
            if validation_warnings:
                workout.parse_status = "failed"
                workout.llm_output_json = {
                    "error": "Invalid structured output from LLM",
                    "warnings": validation_warnings,
                }
                session.flush()
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

            # Persist steps (transactional: delete old, insert new)
            # Delete existing steps
            delete_stmt = delete(WorkoutStep).where(WorkoutStep.workout_id == workout_id)
            session.execute(delete_stmt)

            # Insert new steps
            for parsed_step in parsed.steps:
                # Map target to workout step fields
                target_type: str | None = None
                target_low: float | None = None
                target_high: float | None = None

                if parsed_step.target:
                    target_type_raw = parsed_step.target.get("type")
                    if isinstance(target_type_raw, str):
                        target_type = target_type_raw.lower()
                    target_low_raw = parsed_step.target.get("low")
                    if isinstance(target_low_raw, (int, float)):
                        target_low = float(target_low_raw)
                    else:
                        target_low = None
                    target_high_raw = parsed_step.target.get("high")
                    if isinstance(target_high_raw, (int, float)):
                        target_high = float(target_high_raw)
                    else:
                        target_high = None

                workout_step = WorkoutStep(
                    workout_id=workout_id,
                    order=parsed_step.order,
                    type=parsed_step.type,
                    distance_meters=parsed_step.distance_meters,
                    duration_seconds=parsed_step.duration_seconds,
                    target_metric=target_type,
                    target_min=target_low,
                    target_max=target_high,
                )
                session.add(workout_step)

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
