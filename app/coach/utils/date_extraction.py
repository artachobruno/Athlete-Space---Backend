"""Unified LLM-based date extraction utility.

This module provides robust date extraction from natural language text
using LLM instead of regex patterns. It handles various date formats
and conversational expressions.
"""

from datetime import date, datetime, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.services.llm.model import get_model

# Use cheap model for extraction
EXTRACTION_MODEL = "gpt-4o-mini"


class DateExtractionResult(BaseModel):
    """Structured date extraction result."""

    date: str | None = Field(
        default=None,
        description=(
            "Extracted date in ISO format (YYYY-MM-DD). "
            "If year is missing, infer based on today's date (current year if date hasn't passed, next year otherwise). "
            "Must be a future date if context requires it."
        ),
    )
    confidence: Literal["high", "moderate", "low"] = Field(
        default="low",
        description=(
            "Confidence level of the extraction: 'high' if date is explicit and clear, "
            "'moderate' if some inference needed, 'low' if minimal or ambiguous information"
        ),
    )
    notes: str | None = Field(
        default=None,
        description="Additional context or clarification about the extracted date",
    )


class MultiDateExtractionResult(BaseModel):
    """Structured extraction result for multiple dates (e.g., season start/end)."""

    dates: list[str] = Field(
        default_factory=list,
        description="List of extracted dates in ISO format (YYYY-MM-DD), sorted chronologically",
    )
    start_date: str | None = Field(
        default=None,
        description="First date in YYYY-MM-DD format (e.g., season start)",
    )
    end_date: str | None = Field(
        default=None,
        description="Last date in YYYY-MM-DD format (e.g., season end)",
    )
    confidence: Literal["high", "moderate", "low"] = Field(
        default="low",
        description="Confidence level of the extraction",
    )
    notes: str | None = Field(
        default=None,
        description="Additional context or clarification",
    )


class SessionCountExtractionResult(BaseModel):
    """Structured session count extraction result."""

    total_sessions: int | None = Field(
        default=None,
        description="Total number of sessions extracted from the text",
    )
    hard_sessions: int | None = Field(
        default=None,
        description="Number of hard sessions if mentioned",
    )
    easy_sessions: int | None = Field(
        default=None,
        description="Number of easy sessions if mentioned",
    )
    moderate_sessions: int | None = Field(
        default=None,
        description="Number of moderate sessions if mentioned",
    )
    confidence: Literal["high", "moderate", "low"] = Field(
        default="low",
        description="Confidence level of the extraction",
    )
    notes: str | None = Field(
        default=None,
        description="Additional context or clarification",
    )


def extract_date_from_text(
    text: str,
    context: str | None = None,
    min_date: date | None = None,
    max_date: date | None = None,
) -> date | None:
    """Extract a single date from natural language text using LLM.

    Args:
        text: Text to extract date from
        context: Optional context about what the date represents (e.g., "race date", "season start")
        min_date: Optional minimum date constraint (date must be >= min_date)
        max_date: Optional maximum date constraint (date must be <= max_date)

    Returns:
        Extracted date object or None if extraction fails
    """
    logger.debug(f"Extracting date from text: {text[:100]}...", context=context)

    today = datetime.now(timezone.utc).date()
    today_str = today.strftime("%Y-%m-%d")
    current_year = today.year

    # Build constraint strings
    constraint_parts = []
    if min_date:
        constraint_parts.append(f"The date must be on or after {min_date.isoformat()}")
    if max_date:
        constraint_parts.append(f"The date must be on or before {max_date.isoformat()}")
    if not constraint_parts:
        constraint_parts.append("The date should be in the future (if only month/day given)")
    constraint_str = "\n".join(f"- {c}" for c in constraint_parts)

    context_str = f"Context: {context}\n" if context else ""
    if context:
        constraint_str += f"\n- This is a {context}, so ensure the date makes sense in that context"

    system_prompt = f"""You are a date extraction assistant. Extract a single date from natural language text.

Today's date is {today_str} (year: {current_year}).

{context_str}
Your task:
- Extract a single date from the text
- Return date in YYYY-MM-DD format (ISO format)
- If year is missing, infer based on today's date:
  * If the date (with current year) hasn't passed yet, use current year
  * If the date (with current year) has passed, use next year
- If only a day is mentioned (e.g., "on the 25th"), infer month from context or use null if not possible

Rules:
{constraint_str}
- Only extract information that is explicitly mentioned or clearly implied
- Be conservative - don't guess or infer unless year inference is needed
- Return null if no clear date can be extracted
- Never infer past dates unless explicitly mentioned

Example inputs (assuming today is {today_str}):
- "on the 25th!" -> extract day, infer month from context or return null
- "April 25th" -> {current_year}-04-25 or {current_year + 1}-04-25 (depending on whether date has passed)
- "April 15, 2026" -> 2026-04-15
- "2026-04-15" -> 2026-04-15
- "4/25/2026" -> 2026-04-25
- "next month on the 15th" -> infer based on current date
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=DateExtractionResult,
    )

    try:
        result = agent.run_sync(f"Extract date from this text: {text}")
        extraction = result.output

        if extraction.date:
            try:
                parsed_date = datetime.fromisoformat(extraction.date).date()
                # Validate constraints
                if min_date and parsed_date < min_date:
                    logger.warning(
                        f"Extracted date {parsed_date} is before min_date {min_date}, returning None",
                        text=text[:50],
                    )
                    return None
                if max_date and parsed_date > max_date:
                    logger.warning(
                        f"Extracted date {parsed_date} is after max_date {max_date}, returning None",
                        text=text[:50],
                    )
                    return None

                logger.info(
                    f"Date extraction successful: {parsed_date}",
                    confidence=extraction.confidence,
                    text=text[:50],
                )
            except (ValueError, TypeError) as e:
                logger.exception(f"Failed to parse extracted date '{extraction.date}': {e}")
                return None
            else:
                return parsed_date
        else:
            logger.debug(f"No date extracted from text: {text[:50]}...")
            return None
    except Exception:
        logger.exception(f"Failed to extract date (text={text[:50]})")
        return None


def extract_dates_from_text(
    text: str,
    context: str | None = None,
    min_date: date | None = None,
    max_date: date | None = None,
    expected_count: int | None = None,
) -> tuple[list[date], str | None, str | None]:
    """Extract multiple dates from natural language text using LLM.

    Args:
        text: Text to extract dates from
        context: Optional context about what the dates represent (e.g., "season dates")
        min_date: Optional minimum date constraint
        max_date: Optional maximum date constraint
        expected_count: Optional expected number of dates (e.g., 2 for start/end)

    Returns:
        Tuple of (list of dates, start_date string, end_date string)
        Dates are sorted chronologically. start_date and end_date are ISO strings or None.
    """
    logger.debug(f"Extracting multiple dates from text: {text[:100]}...", context=context, expected_count=expected_count)

    today = datetime.now(timezone.utc).date()
    today_str = today.strftime("%Y-%m-%d")
    current_year = today.year

    # Build constraint strings
    constraint_parts = []
    if min_date:
        constraint_parts.append(f"All dates must be on or after {min_date.isoformat()}")
    if max_date:
        constraint_parts.append(f"All dates must be on or before {max_date.isoformat()}")
    if expected_count:
        constraint_parts.append(f"Expected to find {expected_count} date(s)")
    if not constraint_parts:
        constraint_parts.append("Dates should be in the future (if only month/day given)")
    constraint_str = "\n".join(f"- {c}" for c in constraint_parts)

    context_str = f"Context: {context}\n" if context else ""

    system_prompt = f"""You are a date extraction assistant. Extract multiple dates from natural language text.

Today's date is {today_str} (year: {current_year}).

{context_str}
Your task:
- Extract all dates mentioned in the text
- Return dates in YYYY-MM-DD format (ISO format), sorted chronologically
- Extract start_date (first/earliest date) and end_date (last/latest date)
- If year is missing, infer based on today's date:
  * If the date (with current year) hasn't passed yet, use current year
  * If the date (with current year) has passed, use next year

Rules:
{constraint_str}
- Only extract information that is explicitly mentioned or clearly implied
- Be conservative - don't guess or infer unless year inference is needed
- Return empty list if no clear dates can be extracted
- Never infer past dates unless explicitly mentioned

Example inputs (assuming today is {today_str}):
- "from January 1 to December 31, 2026" -> dates: ["2026-01-01", "2026-12-31"], start_date: "2026-01-01", end_date: "2026-12-31"
- "season from April 15 to October 15" -> extract both dates with year inference
- "January 1, 2026 and March 15, 2026" -> dates: ["2026-01-01", "2026-03-15"], start_date: "2026-01-01", end_date: "2026-03-15"
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=MultiDateExtractionResult,
    )

    try:
        result = agent.run_sync(f"Extract all dates from this text: {text}")
        extraction = result.output

        parsed_dates: list[date] = []
        for date_str in extraction.dates:
            try:
                parsed = datetime.fromisoformat(date_str).date()
                # Validate constraints
                if min_date and parsed < min_date:
                    logger.warning(
                        f"Extracted date {parsed} is before min_date {min_date}, skipping",
                        text=text[:50],
                    )
                    continue
                if max_date and parsed > max_date:
                    logger.warning(
                        f"Extracted date {parsed} is after max_date {max_date}, skipping",
                        text=text[:50],
                    )
                    continue
                parsed_dates.append(parsed)
            except (ValueError, TypeError) as e:
                logger.exception(f"Failed to parse extracted date '{date_str}': {e}")
                continue

        # Sort dates chronologically
        parsed_dates.sort()

        # Extract start and end dates
        start_date_str = extraction.start_date if extraction.start_date else (parsed_dates[0].isoformat() if parsed_dates else None)
        end_date_str = extraction.end_date if extraction.end_date else (parsed_dates[-1].isoformat() if len(parsed_dates) > 1 else None)

        logger.info(
            f"Multi-date extraction successful: {len(parsed_dates)} dates",
            dates=[d.isoformat() for d in parsed_dates],
            start_date=start_date_str,
            end_date=end_date_str,
            confidence=extraction.confidence,
            text=text[:50],
        )
    except Exception:
        logger.exception(f"Failed to extract dates (text={text[:50]})")
        return [], None, None
    else:
        return parsed_dates, start_date_str, end_date_str


def extract_session_count_from_text(text: str) -> int | None:
    """Extract session count from intensity distribution text using LLM.

    Args:
        text: Text describing intensity distribution (e.g., "2 hard sessions, 4 easy sessions")

    Returns:
        Total number of sessions or None if extraction fails
    """
    logger.debug(f"Extracting session count from text: {text[:100]}...")

    system_prompt = """You are a session count extraction assistant. Extract the total number of
training sessions from text describing intensity distribution.

Your task:
- Extract the total number of training sessions mentioned
- Count sessions by type if mentioned (hard, easy, moderate)
- Sum up all session counts to get total

Rules:
- Only extract numbers that clearly represent session counts
- If multiple counts are mentioned, sum them up
- Return the total number of sessions as a single integer
- Return null if no clear session count can be extracted

Example inputs:
- "2 hard sessions, 4 easy sessions" -> total_sessions: 6
- "6 training sessions" -> total_sessions: 6
- "3 hard, 2 easy, 1 moderate" -> total_sessions: 6
- "approximately 5-6 sessions per week" -> total_sessions: 6 (use average)
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=SessionCountExtractionResult,
    )

    try:
        result = agent.run_sync(f"Extract session count from this text: {text}")
        extraction = result.output

        session_count: int | None = None

        if extraction.total_sessions is not None:
            logger.info(
                f"Session count extraction successful: {extraction.total_sessions}",
                confidence=extraction.confidence,
                text=text[:50],
            )
            session_count = extraction.total_sessions
        # Fallback: sum individual counts if available
        elif extraction.hard_sessions or extraction.easy_sessions or extraction.moderate_sessions:
            total = (extraction.hard_sessions or 0) + (extraction.easy_sessions or 0) + (extraction.moderate_sessions or 0)
            if total > 0:
                logger.info(f"Session count extracted by summing components: {total}", text=text[:50])
                session_count = total

        if session_count is None:
            logger.debug(f"No session count extracted from text: {text[:50]}...")
    except Exception:
        logger.exception(f"Failed to extract session count (text={text[:50]})")
        return None
    else:
        return session_count
