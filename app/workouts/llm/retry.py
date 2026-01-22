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
from app.workouts.llm.logging_helpers import (
    log_llm_extracted_fields,
    log_llm_raw_response,
    log_llm_request,
)
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

    system_prompt = (
        "You are a workout structuring engine. "
        "You MUST output valid JSON only, without any markdown code blocks, explanations, or extra text. "
        "The JSON must be parseable and start with '{' and end with '}'."
    )
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=StructuredWorkout,
    )

    validation_errors: list[str] = []
    keyerror_retry_prompt: str | None = None

    for attempt in range(2):  # Initial attempt + one retry
        try:
            logger.info(f"Generating workout steps (attempt {attempt + 1}/2)")

            # Build prompt with validation errors if retrying
            current_prompt = keyerror_retry_prompt if keyerror_retry_prompt else prompt
            if attempt > 0 and validation_errors and not keyerror_retry_prompt:
                error_context = "\n".join(f"- {e}" for e in validation_errors)
                current_prompt = f"""{prompt}

PREVIOUS ATTEMPT VALIDATION ERRORS:
{error_context}

Please fix these errors and output valid JSON only."""

            # Log the actual prompt submitted to LLM
            log_llm_request(
                context="Workout Step Generation (Retry)",
                system_prompt=system_prompt,
                user_prompt=current_prompt,
                attempt=attempt + 1,
            )

            result = await agent.run(current_prompt)

            # Log raw response from LLM (before parsing)
            log_llm_raw_response(
                context="Workout Step Generation (Retry)",
                result=result,
                attempt=attempt + 1,
            )

            workout = result.output

            # Log extracted/parsed fields
            log_llm_extracted_fields(
                context="Workout Step Generation (Retry)",
                parsed_output=workout,
                attempt=attempt + 1,
            )

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
            logger.error(f"Validation error details: {e.errors() if hasattr(e, 'errors') else 'No details available'}")
            if attempt == 0:
                logger.warning(f"JSON parsing failed on attempt {attempt + 1}: {e}. Retrying...")
                continue
            # Second attempt failed
            logger.error(f"JSON parsing failed after retry: {e}")
            raise ParseFailedError(f"Workout parsing failed after retry: {e}") from e

        except KeyError as e:
            error_msg = (
                f"KeyError during JSON parsing: {e}. "
                "This usually indicates the LLM response format is incorrect. "
                "The response may be wrapped in markdown code blocks or contain formatting issues."
            )
            validation_errors = [error_msg]
            logger.error(error_msg)
            if attempt == 0:
                logger.warning(f"JSON parsing KeyError on attempt {attempt + 1}: {e}. Retrying with explicit JSON format instructions...")
                # Update prompt to be even more explicit about JSON format for next attempt
                keyerror_retry_prompt = f"""{prompt}

CRITICAL: Output ONLY valid JSON. Do NOT wrap in markdown code blocks. Do NOT include any text before or after the JSON.
The JSON must start with {{ and end with }}. No explanations, no markdown, just pure JSON."""
                continue
            # Second attempt failed
            logger.error(f"JSON parsing KeyError failed after retry: {e}")
            raise ParseFailedError(
                f"Failed to parse LLM response: KeyError accessing '{e}'. "
                "The LLM may have returned improperly formatted JSON. "
                "Please try again or check the workout notes format."
            ) from e

        except Exception as e:
            logger.exception("LLM call failed")
            error_type = type(e).__name__
            error_msg = str(e)
            logger.error(f"Exception type: {error_type}, message: {error_msg}")
            if attempt == 0:
                logger.warning(f"LLM call failed on attempt {attempt + 1}. Retrying...")
                continue
            raise RuntimeError(f"Failed to generate workout steps after retry: {error_type}: {error_msg}") from e

    # Should not reach here, but handle gracefully
    raise ParseFailedError("Workout parsing failed after all retries")
