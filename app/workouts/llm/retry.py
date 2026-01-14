"""LLM error recovery with one retry.

This module handles retry logic when LLM output fails validation.
One retry only - if still invalid, mark workout as PARSE_FAILED.
"""

from __future__ import annotations

import json

from loguru import logger
from pydantic import ValidationError
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.services.llm.model import get_model
from app.workouts.canonical import StructuredWorkout
from app.workouts.input import ActivityInput
from app.workouts.llm.step_generator import build_workout_prompt
from app.workouts.validation import ValidationError as WorkoutValidationError
from app.workouts.validation import validate_structured_workout


class ParseFailedError(Exception):
    """Raised when workout parsing fails after retry."""

    pass


async def generate_with_retry(
    activity: ActivityInput,
    activity_distance: int | None = None,
) -> StructuredWorkout:
    """Generate structured workout with one retry on validation failure.

    Args:
        activity: Normalized activity input
        activity_distance: Optional total activity distance for validation

    Returns:
        Validated StructuredWorkout

    Raises:
        ParseFailedError: If validation fails after retry
        RuntimeError: If LLM call fails
    """
    model = get_model("openai", USER_FACING_MODEL)
    prompt = build_workout_prompt(activity)

    agent = Agent(
        model=model,
        system_prompt="You are a workout structuring engine. Output JSON only.",
        output_type=StructuredWorkout,
    )

    validation_errors: list[str] = []

    for attempt in range(2):  # Initial attempt + one retry
        try:
            logger.info(f"Generating workout steps (attempt {attempt + 1}/2)")

            # Build prompt with validation errors if retrying
            current_prompt = prompt
            if attempt > 0 and validation_errors:
                error_context = "\n".join(f"- {e}" for e in validation_errors)
                current_prompt = f"""{prompt}

PREVIOUS ATTEMPT VALIDATION ERRORS:
{error_context}

Please fix these errors and output valid JSON only."""

            result = await agent.run(current_prompt)
            workout = result.output

            # Validate the workout
            try:
                validate_structured_workout(workout, activity_distance)
                logger.info(f"Successfully generated and validated workout with {len(workout.steps)} steps")
            except WorkoutValidationError as e:
                validation_errors = [str(e)]
                if attempt == 0:
                    logger.warning(f"Workout validation failed on attempt {attempt + 1}: {e}. Retrying...")
                    continue
                # Second attempt failed
                logger.error(f"Workout validation failed after retry: {e}")
                raise ParseFailedError(f"Workout parsing failed after retry: {e}") from e
            else:
                return workout

        except ValidationError as e:
            validation_errors = [f"JSON parsing error: {e}"]
            if attempt == 0:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}. Retrying...")
                continue
            # Second attempt failed
            logger.error(f"JSON parsing failed after retry: {e}")
            raise ParseFailedError(f"Workout parsing failed after retry: {e}") from e

        except Exception as e:
            logger.exception("LLM call failed")
            if attempt == 0:
                logger.warning(f"LLM call failed on attempt {attempt + 1}. Retrying...")
                continue
            raise RuntimeError(f"Failed to generate workout steps after retry: {type(e).__name__}: {e}") from e

    # Should not reach here, but handle gracefully
    raise ParseFailedError("Workout parsing failed after all retries")
