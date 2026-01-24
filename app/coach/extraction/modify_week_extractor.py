"""LLM extraction for MODIFY → week operations.

This module provides LLM-based attribute extraction for week modifications.
The LLM only emits structured intent - it never modifies plans or applies business logic.
"""

from datetime import date, datetime, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.services.llm.model import get_model

EXTRACTION_MODEL = "gpt-4o-mini"


class ExtractedWeekModification(BaseModel):
    """Extracted week modification attributes from user message.

    This schema is authoritative - the LLM must conform to it exactly.
    All fields are optional to allow partial extraction.
    """

    horizon: Literal["week"] = Field(default="week", description="Always 'week' for this extractor")

    change_type: Literal[
        "reduce_volume",
        "increase_volume",
        "shift_days",
        "replace_day",
    ] | None = Field(
        default=None,
        description="Type of modification: reduce_volume, increase_volume, shift_days, or replace_day",
    )

    # Range (can be null → resolved later)
    start_date: str | None = Field(
        default=None,
        description="Start date in YYYY-MM-DD format. May be relative (e.g., 'this week')",
    )
    end_date: str | None = Field(
        default=None,
        description="End date in YYYY-MM-DD format. May be relative (e.g., 'this week')",
    )

    # Volume modifications
    percent: float | None = Field(
        default=None,
        description="Percentage change as decimal (e.g., 0.2 for 20% reduction). Must be positive.",
    )
    miles: float | None = Field(
        default=None,
        description="Absolute change in miles. Positive for increase, negative for decrease.",
    )

    # Shift days
    shift_map: dict[str, str] | None = Field(
        default=None,
        description="Mapping of old dates to new dates (YYYY-MM-DD format). e.g., {'2026-01-15': '2026-01-16'}",
    )

    # Delegate to day modification
    target_date: str | None = Field(
        default=None,
        description="Target date for replace_day operation in YYYY-MM-DD format",
    )
    day_modification: dict | None = Field(
        default=None,
        description="DayModification dict for replace_day operation",
    )

    reason: str | None = Field(
        default=None,
        description="Reason for modification (e.g., 'fatigue', 'time constraint')",
    )

    def is_complete(self) -> bool:
        """True iff spec has required attributes for execution (change_type)."""
        return self.change_type is not None


async def extract_week_modification_llm(
    user_message: str,
    today: date | None = None,
) -> ExtractedWeekModification:
    """Extract structured week modification attributes from user message.

    This is the LLM extraction layer. It only emits structured intent.
    No business logic, no validation, no plan modification.

    Args:
        user_message: User's modification request
        today: Today's date for relative date resolution (defaults to current date)

    Returns:
        ExtractedWeekModification with extracted attributes

    Raises:
        RuntimeError: If LLM extraction fails
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    today_str = today.strftime("%Y-%m-%d")

    system_prompt = """You are an attribute extraction engine.

Your job is to extract structured workout plan modification intent.

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

- change_type: One of "reduce_volume", "increase_volume", "shift_days", "replace_day"
- percent: Decimal value (0.2 = 20%). Always positive.
- miles: Number of miles. Positive for increase, negative for decrease.
- dates: YYYY-MM-DD format. May be relative (e.g., "this week") - use as-is.
- shift_map: Object with old_date -> new_date mappings (YYYY-MM-DD format)

━━━━━━━━━━━━━━━━━━━
EXAMPLES
━━━━━━━━━━━━━━━━━━━

User: "Cut this week by 20%, I'm exhausted."
→ {
  "change_type": "reduce_volume",
  "percent": 0.2,
  "reason": "fatigue"
}

User: "Move Tuesday workout to Wednesday"
→ {
  "change_type": "shift_days",
  "shift_map": {"2026-01-14": "2026-01-15"}
}

User: "Add 10 miles this week"
→ {
  "change_type": "increase_volume",
  "miles": 10.0
}
"""

    user_prompt = f"""Today's date: {today_str}

User request:
"{user_message}"

Extract attributes according to the ExtractedWeekModification schema.

Notes:
- Distances are in miles
- Percent values must be decimals (0.2 = 20%)
- Dates may be relative (e.g., "this week", "next week") - keep as-is if relative
- If ambiguous, leave fields null
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=ExtractedWeekModification,
    )

    try:
        logger.debug(
            f"LLM Prompt: Week Modification Extraction\n"
            f"System Prompt:\n{system_prompt}\n\n"
            f"User Prompt:\n{user_prompt}",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        result = await agent.run(user_prompt)
        extracted = result.output
    except Exception as e:
        logger.exception(f"Failed to extract week modification attributes: {e}")
        raise RuntimeError(f"LLM extraction failed: {e}") from e
    else:
        logger.info(
            "Week modification attributes extracted",
            change_type=extracted.change_type,
            reason=extracted.reason,
        )

        return extracted
