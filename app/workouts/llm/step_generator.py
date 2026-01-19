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

    system_prompt = "You are a workout structuring engine. Output JSON only."
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=StructuredWorkout,
    )

    try:
        logger.info(f"Generating workout steps from notes for sport: {activity.sport}")
        logger.debug(
            "LLM Prompt: Workout Step Generation",
            system_prompt=system_prompt,
            user_prompt=prompt,
        )
        result = await agent.run(prompt)
        logger.info(f"Successfully generated workout with {len(result.output.steps)} steps")
    except ValidationError as e:
        logger.error(f"LLM output validation failed: {e}")
        raise ValueError(f"LLM generated invalid workout structure: {e}") from e
    except Exception as e:
        logger.exception("LLM call failed")
        raise RuntimeError(f"Failed to generate workout steps: {type(e).__name__}: {e}") from e
    else:
        return result.output
