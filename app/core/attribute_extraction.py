"""Attribute extraction API - authoritative extraction with confidence and evidence.

This module provides the extractor-authoritative pattern:
- Extractor decides what is actually known
- Returns structured output with confidence, evidence, missing_fields, ambiguous_fields
- Orchestrator decides what needs to be known, extractor decides what is known
"""

from datetime import date, datetime, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.coach.services.conversation_progress import get_conversation_progress
from app.coach.tools.plan_race import parse_date_string
from app.services.llm.model import get_model

# Use cheap model for extraction
EXTRACTION_MODEL = "gpt-4o-mini"


class AttributeEvidence(BaseModel):
    """Evidence span for a single extracted attribute."""

    field: str = Field(description="Attribute name (e.g., 'race_distance', 'race_date')")
    text: str = Field(description="Text span from user message that supports this value")


class ExtractedAttributes(BaseModel):
    """Structured output from attribute extractor with confidence and evidence.

    The extractor is authoritative - if it didn't return it, it does not exist.
    """

    # Extracted values (canonical forms)
    values: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict,
        description="Dictionary of extracted attribute values in canonical form",
    )

    # Confidence and evidence
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall confidence in extraction (0.0-1.0)",
    )
    evidence: list[AttributeEvidence] = Field(
        default_factory=list,
        description="Evidence spans from user message for each extracted field",
    )

    # Missing and ambiguous fields
    missing_fields: list[str] = Field(
        default_factory=list,
        description="List of requested fields that were NOT found in the message",
    )
    ambiguous_fields: list[str] = Field(
        default_factory=list,
        description="List of requested fields that are ambiguous or unclear",
    )


async def extract_attributes(
    text: str,
    attributes_requested: list[str],
    conversation_slot_state: dict[str, str | date | int | float | bool | None] | None = None,
    today: date | None = None,
) -> ExtractedAttributes:
    """Extract ONLY the requested attributes from text with confidence and evidence.

    This is the authoritative extractor - if it didn't return it, it does not exist.

    Args:
        text: User message text to extract from
        attributes_requested: List of attribute names to extract (e.g., ["race_distance", "race_date"])
        conversation_slot_state: Current conversation slot state for context-aware extraction
        today: Today's date for date inference (defaults to current date)

    Returns:
        ExtractedAttributes with:
        - values: Dictionary of extracted values (canonical forms)
        - confidence: Overall confidence (0.0-1.0)
        - evidence: List of evidence spans
        - missing_fields: Fields requested but not found
        - ambiguous_fields: Fields that are unclear

    Note:
        This function extracts ONLY the requested attributes.
        If an attribute is not in attributes_requested, it will not be extracted.
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    today_str = today.strftime("%Y-%m-%d")
    current_year = today.year

    # Build conversation context string
    context_parts = []
    if conversation_slot_state:
        if conversation_slot_state.get("race_distance"):
            context_parts.append(f"Known race distance: {conversation_slot_state['race_distance']}")
        if conversation_slot_state.get("race_date"):
            race_date = conversation_slot_state["race_date"]
            if isinstance(race_date, date):
                context_parts.append(f"Known race date: {race_date.isoformat()}")
            elif isinstance(race_date, str):
                context_parts.append(f"Known race date: {race_date}")
        if conversation_slot_state.get("target_time"):
            context_parts.append(f"Known target time: {conversation_slot_state['target_time']}")

    context_str = "\n".join(context_parts) if context_parts else "No previous context."

    # Build attribute descriptions for the prompt
    attribute_descriptions = {
        "race_distance": "Race distance - one of: 5K, 10K, Half Marathon, Marathon, Ultra",
        "race_date": f"Race date in YYYY-MM-DD format (today is {today_str}, year: {current_year})",
        "target_time": "Target finish time in HH:MM:SS format (e.g., 03:00:00 for 3 hours)",
        "weekly_mileage": "Weekly mileage (number in miles per week)",
        "race_name": "Race name (official or informal name)",
    }

    requested_descriptions = []
    for attr in attributes_requested:
        desc = attribute_descriptions.get(attr, attr)
        requested_descriptions.append(f"- {attr}: {desc}")

    attributes_list_str = "\n".join(requested_descriptions)

    system_prompt = f"""You are an authoritative attribute extraction assistant.

Your job is to extract ONLY the requested attributes from the user's message.

Today's date is {today_str} (year: {current_year}).

━━━━━━━━━━━━━━━━━━━
CONVERSATION CONTEXT
━━━━━━━━━━━━━━━━━━━

{context_str}

━━━━━━━━━━━━━━━━━━━
REQUESTED ATTRIBUTES
━━━━━━━━━━━━━━━━━━━

Extract ONLY these attributes:
{attributes_list_str}

━━━━━━━━━━━━━━━━━━━
CORE RULES (STRICT)
━━━━━━━━━━━━━━━━━━━

1. Extract ONLY the requested attributes
2. Return values in canonical forms:
   - race_distance: One of ["5K", "10K", "Half Marathon", "Marathon", "Ultra"]
   - race_date: YYYY-MM-DD format
   - target_time: HH:MM:SS format
   - weekly_mileage: Number (integer or float)
3. Use conversation context to resolve partial answers (e.g., "April 25" + known month)
4. Do NOT invent or guess missing information
5. If an attribute is not mentioned, mark it in missing_fields
6. If an attribute is ambiguous, mark it in ambiguous_fields
7. Provide evidence spans for each extracted value

━━━━━━━━━━━━━━━━━━━
DATE RESOLUTION
━━━━━━━━━━━━━━━━━━━

- Relative dates: "in 4 weeks" → today + 28 days, format as YYYY-MM-DD
- Month/day only: Use conversation context if available, otherwise infer year:
  * If date (with current year) is in the future → use current year
  * If date has passed → use next year
- Never infer past dates
- Normalize all dates to YYYY-MM-DD

━━━━━━━━━━━━━━━━━━━
TIME NORMALIZATION
━━━━━━━━━━━━━━━━━━━

- "sub 3" → 03:00:00
- "under 2 hours" → 02:00:00
- "2:45 marathon" → 02:45:00
- Normalize all times to HH:MM:SS

━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━

Return a JSON object with:
- values: Dictionary of extracted values (canonical forms)
- confidence: Overall confidence (0.0-1.0)
- evidence: List of evidence spans (field + text)
- missing_fields: List of requested fields NOT found
- ambiguous_fields: List of requested fields that are unclear

CRITICAL: If you didn't extract it, it does not exist.
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=ExtractedAttributes,
    )

    try:
        logger.debug(
            "Extracting attributes",
            attributes_requested=attributes_requested,
            text_preview=text[:100],
            has_context=conversation_slot_state is not None,
        )
        user_prompt = f"Extract the requested attributes from this message: {text}"
        logger.debug(
            "LLM Prompt: Attribute Extraction",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        result = await agent.run(user_prompt)
        extracted = result.output

        logger.info(
            "Attribute extraction completed",
            attributes_requested=attributes_requested,
            extracted_count=len(extracted.values),
            missing_count=len(extracted.missing_fields),
            ambiguous_count=len(extracted.ambiguous_fields),
            confidence=extracted.confidence,
        )
    except Exception:
        logger.exception("Failed to extract attributes")
        # Return empty extraction on failure (non-blocking)
        return ExtractedAttributes(
            values={},
            confidence=0.0,
            evidence=[],
            missing_fields=attributes_requested.copy(),
            ambiguous_fields=[],
        )
    else:
        return extracted
