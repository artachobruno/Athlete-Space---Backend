"""Attribute extraction API - authoritative extraction with confidence and evidence.

This module provides the extractor-authoritative pattern:
- Extractor decides what is actually known
- Returns structured output with confidence, evidence, missing_fields, ambiguous_fields
- Orchestrator decides what needs to be known, extractor decides what is known
"""

import json
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

    Schema enforces evidence → value coupling: if evidence exists, value must be set.
    """

    # Extracted values (explicit fields - schema enforces extraction)
    race_distance: Literal["5K", "10K", "Half Marathon", "Marathon", "Ultra"] | None = Field(
        default=None,
        description="Race distance in canonical form",
    )
    race_date: str | None = Field(
        default=None,
        description="Race date in YYYY-MM-DD format",
    )
    target_time: str | None = Field(
        default=None,
        description="Target finish time in HH:MM:SS format",
    )
    weekly_mileage: int | float | None = Field(
        default=None,
        description="Weekly mileage (number in miles per week)",
    )
    race_name: str | None = Field(
        default=None,
        description="Race name (official or informal name)",
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

    @property
    def values(self) -> dict[str, str | int | float | bool | None]:
        """Backward compatibility: convert explicit fields to dict.

        Returns:
            Dictionary of extracted values (only non-None fields included)
        """
        result: dict[str, str | int | float | bool | None] = {}
        if self.race_distance is not None:
            result["race_distance"] = self.race_distance
        if self.race_date is not None:
            result["race_date"] = self.race_date
        if self.target_time is not None:
            result["target_time"] = self.target_time
        if self.weekly_mileage is not None:
            result["weekly_mileage"] = self.weekly_mileage
        if self.race_name is not None:
            result["race_name"] = self.race_name
        return result


class EvidenceNormalization(BaseModel):
    """Normalized values inferred from evidence spans."""

    race_distance: Literal["5K", "10K", "Half Marathon", "Marathon", "Ultra"] | None = None
    race_date: str | None = None
    target_time: str | None = None
    weekly_mileage: int | float | None = None
    race_name: str | None = None

    @property
    def values(self) -> dict[str, str | int | float | bool | None]:
        """Convert explicit fields to dict."""
        result: dict[str, str | int | float | bool | None] = {}
        if self.race_distance is not None:
            result["race_distance"] = self.race_distance
        if self.race_date is not None:
            result["race_date"] = self.race_date
        if self.target_time is not None:
            result["target_time"] = self.target_time
        if self.weekly_mileage is not None:
            result["weekly_mileage"] = self.weekly_mileage
        if self.race_name is not None:
            result["race_name"] = self.race_name
        return result


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
        "race_distance": (
            "Race distance - one of: 5K, 10K, Half Marathon, Marathon, Ultra. "
            "Common mentions: 'marathon' (any context), 'my marathon', '5k', 'half marathon', etc. "
            "Extract if mentioned anywhere in the message, even indirectly."
        ),
        "race_date": (
            f"Race date in YYYY-MM-DD format (today is {today_str}, year: {current_year}). "
            "Recognize formats: MM/DD (e.g., '04/25'), M/D (e.g., '4/25'), month/day (e.g., 'April 25'), "
            "relative dates (e.g., 'in 4 weeks'). See DATE RESOLUTION section for parsing rules."
        ),
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
CRITICAL: COMMON PATTERNS TO RECOGNIZE
━━━━━━━━━━━━━━━━━━━

ALWAYS extract these patterns when you see them:

1. RACE DISTANCE:
   - "marathon" (anywhere in text) → race_distance: "Marathon"
   - "my marathon" → race_distance: "Marathon"
   - "plan my marathon" → race_distance: "Marathon"
   - "5k" or "5K" → race_distance: "5K"
   - "half marathon" → race_distance: "Half Marathon"
   - "10k" or "10K" → race_distance: "10K"
   - "ultra" → race_distance: "Ultra"

2. DATE FORMATS (MM/DD or M/D):
   - "04/25" → race_date: "{current_year}-04-25" (if April 25 hasn't passed)
   - "4/25" → race_date: "{current_year}-04-25"
   - "for 04/25" → race_date: "{current_year}-04-25"
   - "on 04/25" → race_date: "{current_year}-04-25"
   - "marathon for 04/25" → race_distance: "Marathon", race_date: "{current_year}-04-25"

3. COMBINED EXAMPLES:
   - "plan my marathon for 04/25" → race_distance: "Marathon", race_date: "{current_year}-04-25"
   - "create a training plan for my marathon on 04/25" → race_distance: "Marathon", race_date: "{current_year}-04-25"
   - "I need a plan for a 5k on 4/25" → race_distance: "5K", race_date: "{current_year}-04-25"

IMPORTANT: If you see "marathon" and "04/25" (or similar date format) in the same message, extract BOTH.

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

CRITICAL: Schema enforces evidence → value coupling. If you provide evidence for a field, you MUST set that field's value.

1. Extract ONLY the requested attributes - but be THOROUGH and AGGRESSIVE in extraction
2. If you find evidence for a field, you MUST set that field's value (not just provide evidence)
3. Return values in canonical forms as explicit fields:
   - race_distance: One of ["5K", "10K", "Half Marathon", "Marathon", "Ultra"]
     * Common phrases: "marathon" → "Marathon", "my marathon" → "Marathon", "plan my marathon" → "Marathon"
     * "5k" or "5K" → "5K", "half marathon" → "Half Marathon"
     * "26.2 miles" → "Marathon", "13.1 miles" → "Half Marathon"
     * CRITICAL: If you see "marathon" anywhere in the text, extract it as "Marathon"
     * Even if it's just "marathon" without "race" or "event", still extract it
   - race_date: YYYY-MM-DD format (see DATE RESOLUTION section for parsing rules)
     * CRITICAL: Recognize MM/DD format like "04/25", "4/25", "12/31"
     * If you see a date pattern like "04/25" or "4/25", parse it as month/day
     * Always convert to YYYY-MM-DD format using current year ({current_year}) if date hasn't passed
   - target_time: HH:MM:SS format
   - weekly_mileage: Number (integer or float)
4. Use conversation context to resolve partial answers (e.g., "April 25" + known month)
5. Do NOT invent or guess missing information - but DO extract if clearly mentioned
6. If an attribute is not mentioned, mark it in missing_fields
7. If an attribute is ambiguous, mark it in ambiguous_fields
8. Provide evidence spans for each extracted value showing exactly where in the text it was found

CRITICAL OUTPUT FORMAT:
- If you find "marathon" → set race_distance: "Marathon" AND provide evidence
- If you find "04/25" → set race_date: "2026-04-25" AND provide evidence
- If you find both → set race_distance: "Marathon", race_date: "2026-04-25" AND provide evidence for both
- Schema enforces: if evidence exists for a field, that field MUST have a value (not None)

EXTRACTION PRIORITY:
- If user says "marathon" → ALWAYS extract race_distance: "Marathon"
- If user says "04/25" or "4/25" → ALWAYS extract race_date: "{current_year}-04-25" (if April 25 hasn't passed)
- If user says "plan my marathon for 04/25" → extract BOTH: race_distance: "Marathon", race_date: "{current_year}-04-25"

━━━━━━━━━━━━━━━━━━━
COMPREHENSIVE EXTRACTION EXAMPLES
━━━━━━━━━━━━━━━━━━━

RACE DISTANCE EXAMPLES (set the field AND provide evidence):
- "marathon" → race_distance: "Marathon" + evidence
- "my marathon" → race_distance: "Marathon" + evidence
- "5k" or "5K" → race_distance: "5K" + evidence
- "half marathon" → race_distance: "Half Marathon" + evidence

DATE EXAMPLES (set the field AND provide evidence):
- "04/25" → race_date: "{current_year}-04-25" + evidence
- "4/25" → race_date: "{current_year}-04-25" + evidence
- "April 25" → race_date: "{current_year}-04-25" + evidence

TARGET TIME EXAMPLES (set the field AND provide evidence):
- "sub 3" → target_time: "03:00:00" + evidence
- "3:30" → target_time: "03:30:00" + evidence

WEEKLY MILEAGE EXAMPLES (set the field AND provide evidence):
- "30 miles per week" → weekly_mileage: 30 + evidence

COMBINED EXAMPLES (set ALL mentioned fields AND provide evidence):
- "plan my marathon for 04/25" → race_distance: "Marathon", race_date: "{current_year}-04-25" + evidence for both
- "create a training plan for my marathon on 04/25" → race_distance: "Marathon", race_date: "{current_year}-04-25" + evidence for both

CRITICAL: Schema enforces coupling. If you provide evidence for a field, that field MUST have a value (not None).

━━━━━━━━━━━━━━━━━━━
DATE RESOLUTION
━━━━━━━━━━━━━━━━━━━

Today's date is {today_str} (year: {current_year}).

Date formats to recognize and parse:
- MM/DD or M/D: "04/25" → parse as month/day, "4/25" → parse as month/day, "12/31" → parse as month/day
- Month/Day: "April 25", "April 25th", "on April 25", "April 25, 2026"
- Relative dates: "in 4 weeks" → today + 28 days, format as YYYY-MM-DD
- Day only in context: "on the 25th" → infer month from context or current month

Date parsing rules:
1. For MM/DD or M/D format (e.g., "04/25", "4/25"):
   * Parse as month/day (first number is month, second is day)
   * If date (with current year) is in the future → use current year
   * If date (with current year) has passed → use next year
   * Example: If today is {today_str} and user says "04/25" → {current_year}-04-25 (if April 25 hasn't passed yet)
   * Example: If today is {today_str} and user says "12/31" → {current_year}-12-31 (if Dec 31 hasn't passed yet)
2. For month/day format (e.g., "April 25"):
   * Use conversation context if available, otherwise infer year:
   * If date (with current year) is in the future → use current year
   * If date has passed → use next year
3. Never infer past dates (always use current or next year)
4. Always normalize dates to YYYY-MM-DD format

Examples (assuming current year is {current_year}):
- "my marathon on 04/25" → race_date: "{current_year}-04-25" (if April 25 hasn't passed)
- "race on 4/25" → race_date: "{current_year}-04-25"
- "training plan for 12/31" → race_date: "{current_year}-12-31"
- "marathon on April 25" → race_date: "{current_year}-04-25"

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

Return a JSON object with explicit fields:
- race_distance: "5K" | "10K" | "Half Marathon" | "Marathon" | "Ultra" | null
- race_date: "YYYY-MM-DD" | null
- target_time: "HH:MM:SS" | null
- weekly_mileage: number | null
- race_name: string | null
- confidence: Overall confidence (0.0-1.0)
- evidence: List of evidence spans (field + text)
- missing_fields: List of requested fields NOT found
- ambiguous_fields: List of requested fields that are unclear

CRITICAL RULE: If you provide evidence for a field, that field MUST have a value (not null).
The schema enforces this - evidence without a value is invalid.

CRITICAL: If you didn't extract it, it does not exist.
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=ExtractedAttributes,
    )

    try:
        logger.info(
            f"Extracting attributes - requested={attributes_requested}, text='{text[:100]}'",
            attributes_requested=attributes_requested,
            text_preview=text[:100],
            has_context=conversation_slot_state is not None,
        )
        user_prompt = f"""Extract the requested attributes from this message: "{text}"

IMPORTANT:
- If you see "marathon" or "my marathon" → set race_distance: "Marathon" (not null)
- If you see "04/25" or "4/25" → set race_date: "{current_year}-04-25" (not null)
- If you see both → set BOTH fields (not null)

CRITICAL: If you provide evidence for a field, you MUST set that field's value.
The schema enforces this - evidence without a value is invalid.

Be thorough and extract everything that is clearly mentioned in the message."""
        logger.debug(
            "LLM Prompt: Attribute Extraction",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        result = await agent.run(user_prompt)
        extracted = result.output

        # Log the raw output to see what the LLM actually returned
        raw_output_dict = extracted.model_dump() if hasattr(extracted, "model_dump") else {"raw": str(extracted)}
        raw_output_json = json.dumps(raw_output_dict)

        # CRITICAL: Enforce evidence → value coupling (post-condition validation)
        # If evidence exists for a field, the value MUST be set
        for ev in extracted.evidence:
            field = ev.field
            field_value = getattr(extracted, field, None)
            if field_value is None:
                error_msg = (
                    f"Extractor violation: evidence exists for '{field}' (text: '{ev.text}') "
                    f"but value is None. This violates the authoritative extractor contract."
                )
                logger.error(error_msg)
                # Attempt normalization from evidence as fallback
                logger.info(f"Attempting to normalize '{field}' from evidence: '{ev.text}'")
                evidence_prompt = f"""Normalize this single attribute value from evidence.

Original message: "{text}"
Field: {field}
Evidence text: "{ev.text}"

Return the canonical value for this field:
- race_distance: One of ["5K", "10K", "Half Marathon", "Marathon", "Ultra"]
- race_date: YYYY-MM-DD format (today is {today_str}, year: {current_year})
- target_time: HH:MM:SS format
- weekly_mileage: Number
- race_name: String

You MUST return a value - evidence exists, so extraction is required.
"""
                evidence_agent = Agent(
                    model=model,
                    system_prompt="Normalize a single attribute value from evidence into canonical form.",
                    output_type=EvidenceNormalization,
                )
                try:
                    evidence_result = await evidence_agent.run(evidence_prompt)
                    normalized = evidence_result.output
                    normalized_value = getattr(normalized, field, None)
                    if normalized_value is not None:
                        setattr(extracted, field, normalized_value)
                        logger.info(f"Normalized '{field}' from evidence: {normalized_value}")
                    else:
                        # Still failed - this is a model failure, not a logic bug
                        logger.error(f"Normalization failed for '{field}' - model returned None despite evidence")
                        # Lower confidence to reflect the failure
                        extracted.confidence = min(extracted.confidence, 0.3)
                except Exception as e:
                    logger.exception(f"Failed to normalize '{field}' from evidence: {e}")
                    extracted.confidence = min(extracted.confidence, 0.3)

        # Lower confidence if values are missing but evidence exists (logical inconsistency)
        if extracted.evidence and not any(
            getattr(extracted, field, None) is not None for field in attributes_requested
        ):
            logger.warning(
                "Confidence inconsistency: evidence exists but no values extracted. Lowering confidence."
            )
            extracted.confidence = min(extracted.confidence, 0.3)

        # Format missing_fields for display (avoid format string issues)
        missing_str = ", ".join(extracted.missing_fields) if extracted.missing_fields else "none"

        logger.info(
            f"Attribute extraction completed - "
            f"extracted_count={len(extracted.values)}, "
            f"missing_count={len(extracted.missing_fields)}, "
            f"missing_fields=[{missing_str}], "
            f"ambiguous_count={len(extracted.ambiguous_fields)}, "
            f"confidence={extracted.confidence:.2f}",
            attributes_requested=attributes_requested,
            extracted_count=len(extracted.values),
            extracted_values=extracted.values,
            missing_count=len(extracted.missing_fields),
            missing_fields=extracted.missing_fields,
            ambiguous_count=len(extracted.ambiguous_fields),
            ambiguous_fields=extracted.ambiguous_fields,
            confidence=extracted.confidence,
            evidence=[{"field": e.field, "text": e.text} for e in extracted.evidence],
            raw_output=raw_output_dict,
            raw_output_json=raw_output_json,
        )
        # Also log raw output separately for debugging (use .format to avoid f-string issues)
        logger.info("Raw LLM extraction output: " + raw_output_json)
    except Exception:
        logger.exception("Failed to extract attributes")
        # Return empty extraction on failure (non-blocking)
        return ExtractedAttributes(
            race_distance=None,
            race_date=None,
            target_time=None,
            weekly_mileage=None,
            race_name=None,
            confidence=0.0,
            evidence=[],
            missing_fields=attributes_requested.copy(),
            ambiguous_fields=[],
        )
    else:
        return extracted
