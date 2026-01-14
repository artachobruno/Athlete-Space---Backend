"""LLM parser for converting natural language workout notes to structured steps.

This module provides a PURE function that parses workout notes using LLM.
No database operations, no side effects.
"""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent

from app.coach.config.models import USER_FACING_MODEL
from app.services.llm.model import get_model


class ParsedStep(BaseModel):
    """Parsed workout step from LLM output."""

    order: int = Field(description="Step order (1-indexed)")
    type: str = Field(description="Step type: warmup | steady | interval | cooldown | rest")
    distance_meters: int | None = Field(default=None, description="Distance in meters")
    duration_seconds: int | None = Field(default=None, description="Duration in seconds")
    target: dict[str, str | float | None] | None = Field(default=None, description="Target specification")


class ParsedWorkout(BaseModel):
    """Parsed workout from LLM output."""

    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score")
    steps: list[ParsedStep] = Field(description="List of workout steps")
    warnings: list[str] = Field(default_factory=list, description="Warnings from parsing")


def parse_workout_notes(
    *,
    sport: str,
    notes: str,
    total_distance_meters: int | None = None,
    total_duration_seconds: int | None = None,
) -> ParsedWorkout:
    """Parse workout notes into structured steps via LLM.

    This is a PURE function - no side effects, no database operations.

    Args:
        sport: Sport type (run, bike, swim)
        notes: Natural language workout notes
        total_distance_meters: Optional total distance in meters
        total_duration_seconds: Optional total duration in seconds

    Returns:
        ParsedWorkout with structured steps

    Raises:
        ValueError: If LLM fails to parse or returns invalid structure
        RuntimeError: If LLM call fails
    """
    if not notes or not notes.strip():
        raise ValueError("Notes cannot be empty")

    # Build prompt
    distance_str = str(total_distance_meters) if total_distance_meters else "null"
    duration_str = str(total_duration_seconds) if total_duration_seconds else "null"

    system_prompt = """You are a professional endurance coach.

You MUST follow these rules:

- Output valid JSON only.
- Do not include explanations or text outside JSON.
- Steps MUST sum to the provided total distance OR total duration (±10%).
- If exact breakdown is unclear, mark the output as ambiguous.
- Never invent distances, durations, or repetitions.
- Prefer distance-based steps over time-based steps when both are present."""
    user_prompt = f"""Parse the following workout notes into structured steps.

Sport: {sport}
Total distance (meters): {distance_str}
Total duration (seconds): {duration_str}

Notes:
{notes}

OUTPUT REQUIREMENTS:
- Output JSON ONLY
- No prose
- No markdown
- No explanations

JSON SCHEMA:
{{
  "confidence": 0.0-1.0,
  "steps": [
    {{
      "order": 1,
      "type": "warmup" | "steady" | "interval" | "cooldown" | "rest",
      "distance_meters": 3200 or null,
      "duration_seconds": null or seconds,
      "target": null or {{
        "type": "pace" | "hr" | "power",
        "low": value or null,
        "high": value or null
      }}
    }}
  ],
  "warnings": []
}}

RULES:
- Steps must sum to total distance OR total duration (±10% tolerance)
- Use step types: warmup, steady, interval, cooldown, rest
- Each step must have either distance_meters OR duration_seconds (not both)
- Order steps sequentially (1, 2, 3, ...)
- If target is specified, include type and low/high values
- If you cannot determine exact breakdown, set confidence < 0.6 and mark as ambiguous
"""

    try:
        model = get_model("openai", USER_FACING_MODEL)
        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            output_type=ParsedWorkout,
        )

        logger.info("Calling LLM to parse workout notes", sport=sport, notes_length=len(notes))
        result = agent.run_sync(user_prompt)
        parsed_workout = result.output

        logger.info(
            "Workout notes parsed successfully",
            sport=sport,
            step_count=len(parsed_workout.steps),
            confidence=parsed_workout.confidence,
        )
    except ValidationError as e:
        logger.error(f"LLM output validation failed: {e}")
        raise ValueError(f"LLM generated invalid workout structure: {e}") from e
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"LLM parsing failed: {type(e).__name__}: {e}", exc_info=True)
        raise RuntimeError(f"Failed to parse workout notes: {type(e).__name__}: {e}") from e
    else:
        return parsed_workout
