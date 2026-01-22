# ❗ Workout instructions and steps must be generated once (planner)
# Never reconstructed downstream
# All workout structure must come from workout.steps - never parse from text

"""LLM-based workout step generation.

This module handles the CORE SYSTEM: converting notes → structured workout.
The LLM is responsible for semantic parsing and structuring.
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


def build_workout_prompt(activity: ActivityInput) -> str:
    """Build the strict, non-negotiable prompt for workout structuring.

    Args:
        activity: Normalized activity input

    Returns:
        Complete prompt string
    """
    distance_str = str(activity.total_distance_meters) if activity.total_distance_meters else "null"
    duration_str = str(activity.total_duration_seconds) if activity.total_duration_seconds else "null"
    notes_str = activity.notes if activity.notes else "(no notes provided)"

    return f"""SYSTEM:
You are a workout structuring engine.

USER INPUT:
Sport: {activity.sport}
Total distance (meters): {distance_str}
Total duration (seconds): {duration_str}
Notes:
{notes_str}

TASK:
Convert the notes into a structured workout.

RULES:
- Output JSON ONLY
- Do NOT include explanations
- Each step must include:
  - order
  - name
  - intensity
- Use distance_meters OR duration_seconds (not both)
- Repeats must be explicit
- Recovery steps must set is_recovery=true
- Use only these intensities:
  easy, tempo, lt2, threshold, vo2, flow, rest
- Do NOT invent steps not implied by the notes
- Distances must sum to the total distance when possible

OUTPUT FORMAT:
{{
  "sport": "{activity.sport}",
  "total_distance_meters": {distance_str},
  "total_duration_seconds": {duration_str},
  "steps": [...]
}}"""


async def generate_steps_from_notes(activity: ActivityInput) -> StructuredWorkout:
    """Generate structured workout steps from activity notes using LLM.

    This is the CORE SYSTEM that converts natural language notes
    into structured workout steps.

    Args:
        activity: Normalized activity input with notes

    Returns:
        StructuredWorkout with parsed steps

    Raises:
        ValueError: If LLM fails to generate valid workout
        RuntimeError: If LLM call fails
    """
    if not activity.notes:
        raise ValueError("Cannot generate workout steps without notes")

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

    try:
        logger.info(f"Generating workout steps from notes for sport: {activity.sport}")

        # Log the actual prompt submitted to LLM
        log_llm_request(
            context="Workout Step Generation",
            system_prompt=system_prompt,
            user_prompt=prompt,
        )

        result = await agent.run(prompt)

        # Log raw response from LLM (before parsing)
        log_llm_raw_response(
            context="Workout Step Generation",
            result=result,
        )

        # Log extracted/parsed fields
        parsed_output = result.output
        log_llm_extracted_fields(
            context="Workout Step Generation",
            parsed_output=parsed_output,
        )

        logger.info(f"Successfully generated workout with {len(result.output.steps)} steps")
    except ValidationError as e:
        logger.error(f"LLM output validation failed: {e}")
        logger.error(f"Validation error details: {e.errors() if hasattr(e, 'errors') else 'No details available'}")
        raise ValueError(f"LLM generated invalid workout structure: {e}") from e
    except KeyError as e:
        logger.error(f"KeyError during JSON parsing: {e}")
        logger.error(
            "This usually indicates the LLM response format is incorrect. "
            "The response may be wrapped in markdown code blocks or contain formatting issues."
        )
        raise RuntimeError(
            f"Failed to parse LLM response: KeyError accessing '{e}'. "
            "The LLM may have returned improperly formatted JSON. "
            "Please try again or check the workout notes format."
        ) from e
    except Exception as e:
        logger.exception("LLM call failed")
        error_type = type(e).__name__
        error_msg = str(e)
        logger.error(f"Exception type: {error_type}, message: {error_msg}")
        raise RuntimeError(f"Failed to generate workout steps: {error_type}: {error_msg}") from e
    else:
        return result.output
