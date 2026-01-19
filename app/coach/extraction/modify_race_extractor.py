"""LLM extraction for MODIFY → race operations.

This module provides LLM-based attribute extraction for race modifications.
The LLM only emits structured intent - it never modifies plans or applies business logic.
"""

from datetime import date, datetime, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.services.llm.model import get_model

EXTRACTION_MODEL = "gpt-4o-mini"


class ExtractedRaceModification(BaseModel):
    """Extracted race modification attributes from user message.

    This schema is authoritative - the LLM must conform to it exactly.
    All fields are optional to allow partial extraction.
    """

    horizon: Literal["race"] = Field(default="race", description="Always 'race' for this extractor")

    change_type: Literal[
        "change_date",
        "change_distance",
        "change_priority",
        "change_taper",
    ] | None = Field(
        default=None,
        description="Type of modification: change_date, change_distance, change_priority, or change_taper",
    )

    new_race_date: str | None = Field(
        default=None,
        description="New race date in YYYY-MM-DD format. May be relative (e.g., 'next month')",
    )
    new_distance_km: float | None = Field(
        default=None,
        description="New race distance in kilometers",
    )
    new_priority: Literal["A", "B", "C"] | None = Field(
        default=None,
        description="New race priority: A (highest), B, or C (lowest)",
    )
    new_taper_weeks: int | None = Field(
        default=None,
        description="New taper length in weeks",
    )

    reason: str | None = Field(
        default=None,
        description="Reason for modification (e.g., 'Race moved by organizer')",
    )


async def extract_race_modification_llm(
    user_message: str,
    today: date | None = None,
) -> ExtractedRaceModification:
    """Extract structured race modification attributes from user message.

    This is the LLM extraction layer. It only emits structured intent.
    No business logic, no validation, no plan modification.

    Args:
        user_message: User's modification request
        today: Today's date for relative date resolution (defaults to current date)

    Returns:
        ExtractedRaceModification with extracted attributes

    Raises:
        RuntimeError: If LLM extraction fails
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    today_str = today.strftime("%Y-%m-%d")

    system_prompt = """You are an attribute extraction engine.

Your job is to extract structured race modification intent.

━━━━━━━━━━━━━━━━━━━
CORE RULES (STRICT)
━━━━━━━━━━━━━━━━━━━

1. Output VALID JSON ONLY
2. Follow the schema exactly
3. Do NOT apply business logic
4. Do NOT infer missing values
5. Use null when information is missing
6. Do NOT explain anything

━━━━━━━━━━━━━━━━━━━
SCHEMA RULES
━━━━━━━━━━━━━━━━━━━

- change_type: One of "change_date", "change_distance", "change_priority", "change_taper"
- new_race_date: YYYY-MM-DD format. May be relative (e.g., "next month") - keep as-is if relative
- new_distance_km: Number in kilometers (e.g., 42.2 for marathon, 21.1 for half)
- new_priority: One of "A", "B", "C"
- new_taper_weeks: Integer (e.g., 2, 3, 4)

━━━━━━━━━━━━━━━━━━━
EXAMPLES
━━━━━━━━━━━━━━━━━━━

User: "My race is now on October 18th"
→ {
  "change_type": "change_date",
  "new_race_date": "2026-10-18",
  "reason": "Race date changed"
}

User: "Change my race to a half marathon"
→ {
  "change_type": "change_distance",
  "new_distance_km": 21.1,
  "reason": "Distance changed to half marathon"
}

User: "Make this a B priority race"
→ {
  "change_type": "change_priority",
  "new_priority": "B",
  "reason": "Priority changed"
}

User: "I want a 3 week taper instead"
→ {
  "change_type": "change_taper",
  "new_taper_weeks": 3,
  "reason": "Taper length changed"
}
"""

    user_prompt = f"""Today's date: {today_str}

User request:
"{user_message}"

Extract attributes according to the ExtractedRaceModification schema.

Notes:
- Distances are in kilometers
- Dates may be relative (e.g., "next month", "two weeks later") - keep as-is if relative
- Priority must be exactly "A", "B", or "C"
- If ambiguous, leave fields null
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=ExtractedRaceModification,
    )

    try:
        logger.debug(
            f"LLM Prompt: Race Modification Extraction\n"
            f"System Prompt:\n{system_prompt}\n\n"
            f"User Prompt:\n{user_prompt}",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        result = await agent.run(user_prompt)
        extracted = result.output
    except Exception as e:
        logger.exception(f"Failed to extract race modification attributes: {e}")
        raise RuntimeError(f"LLM extraction failed: {e}") from e
    else:
        logger.info(
            "Race modification attributes extracted",
            change_type=extracted.change_type,
            reason=extracted.reason,
        )

        return extracted
