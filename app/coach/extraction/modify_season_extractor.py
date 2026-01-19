"""LLM extraction for MODIFY → season operations.

This module provides LLM-based attribute extraction for season modifications.
The LLM only emits structured intent - it never modifies plans or applies business logic.
"""

from datetime import date, datetime, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.services.llm.model import get_model

EXTRACTION_MODEL = "gpt-4o-mini"

SeasonChangeType = Literal[
    "reduce_volume",
    "increase_volume",
    "shift_season",
    "extend_phase",
    "reduce_phase",
    "protect_race",
]


class ExtractedSeasonModification(BaseModel):
    """Extracted season modification attributes from user message.

    This schema is authoritative - the LLM must conform to it exactly.
    All fields are optional to allow partial extraction.
    """

    change_type: SeasonChangeType | None = Field(
        default=None,
        description="Type of modification: reduce_volume, increase_volume, shift_season, extend_phase, reduce_phase, protect_race",
    )

    season_ref: str | None = Field(
        default=None,
        description="Season reference (e.g., 'this season', 'spring build', 'winter base')",
    )

    phase: str | None = Field(
        default=None,
        description="Training phase: base, build, peak, taper",
    )

    percent: float | None = Field(
        default=None,
        description="Percentage change as decimal (e.g., 0.2 for 20% reduction). Must be positive.",
    )

    miles: float | None = Field(
        default=None,
        description="Absolute change in miles. Positive for increase, negative for decrease.",
    )

    weeks: int | None = Field(
        default=None,
        description="Number of weeks to extend or reduce phase",
    )

    reason: str | None = Field(
        default=None,
        description="Reason for modification (e.g., 'fatigue', 'time constraint', 'injury recovery')",
    )


async def extract_modify_season(text: str) -> ExtractedSeasonModification:
    """Extract structured season modification attributes from user message.

    This is the LLM extraction layer. It only emits structured intent.
    No business logic, no validation, no plan modification.

    Args:
        text: User's modification request

    Returns:
        ExtractedSeasonModification with extracted attributes

    Raises:
        RuntimeError: If LLM extraction fails
    """
    system_prompt = """You are an attribute extraction engine.

Your job is to extract structured season-level training plan modification intent.

━━━━━━━━━━━━━━━━━━━
CORE RULES (STRICT)
━━━━━━━━━━━━━━━━━━━

1. Output VALID JSON ONLY
2. Follow the schema exactly
3. Do NOT apply business logic
4. Do NOT infer missing values
5. Use null when information is missing
6. Do NOT explain anything
7. Do NOT extract dates or week numbers
8. Do NOT do math

━━━━━━━━━━━━━━━━━━━
SCHEMA RULES
━━━━━━━━━━━━━━━━━━━

- change_type: One of "reduce_volume", "increase_volume", "shift_season", "extend_phase", "reduce_phase", "protect_race"
- season_ref: Text reference like "this season", "spring build", "winter base" (keep as-is, no resolution)
- phase: One of "base", "build", "peak", "taper" (keep as-is, no resolution)
- percent: Decimal value (0.2 = 20%). Always positive.
- miles: Number of miles. Positive for increase, negative for decrease.
- weeks: Number of weeks (for extend_phase/reduce_phase). Positive integer.
- reason: Free text reason

━━━━━━━━━━━━━━━━━━━
EXAMPLES
━━━━━━━━━━━━━━━━━━━

User: "Cut this season by 20%, I'm exhausted."
→ {
  "change_type": "reduce_volume",
  "season_ref": "this season",
  "percent": 0.2,
  "reason": "fatigue"
}

User: "Extend the base phase by 2 weeks"
→ {
  "change_type": "extend_phase",
  "phase": "base",
  "weeks": 2
}

User: "Add 50 miles to the build phase"
→ {
  "change_type": "increase_volume",
  "phase": "build",
  "miles": 50.0
}
"""

    user_prompt = f"""User request:
"{text}"

Extract attributes according to the ExtractedSeasonModification schema.

Notes:
- Distances are in miles
- Percent values must be decimals (0.2 = 20%)
- Do NOT extract dates or week numbers
- Do NOT resolve season_ref or phase to concrete values
- If ambiguous, leave fields null
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=ExtractedSeasonModification,
    )

    try:
        logger.debug(
            "LLM Prompt: Season Modification Extraction",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        result = await agent.run(user_prompt)
        extracted = result.output
    except Exception as e:
        logger.exception(f"Failed to extract season modification attributes: {e}")
        raise RuntimeError(f"LLM extraction failed: {e}") from e
    else:
        logger.info(
            "Season modification attributes extracted",
            change_type=extracted.change_type,
            season_ref=extracted.season_ref,
            phase=extracted.phase,
            reason=extracted.reason,
        )

        return extracted
