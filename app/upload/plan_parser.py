"""Parser for training plan uploads from CSV or free text.

Handles parsing training plans from:
- CSV files (date, workout, distance, intensity)
- Free text descriptions ("Mon easy 8, Tue intervals...")
"""

from __future__ import annotations

import csv
import io
import re
from contextlib import suppress
from datetime import date, datetime, timedelta, timezone
from typing import Any

from loguru import logger
from pydantic import BaseModel, field_validator


class ParsedSessionUpload(BaseModel):
    """Parsed training session data from upload."""

    date: datetime
    time: str | None = None
    type: str
    title: str
    duration_minutes: int | None = None
    distance_km: float | None = None
    intensity: str | None = None
    notes: str | None = None
    week_number: int | None = None

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration(cls, v: int | None) -> int | None:
        """Validate duration is positive if provided."""
        if v is not None and v <= 0:
            raise ValueError("duration_minutes must be > 0")
        return v

    @field_validator("distance_km")
    @classmethod
    def validate_distance(cls, v: float | None) -> float | None:
        """Validate distance is positive if provided."""
        if v is not None and v <= 0:
            raise ValueError("distance_km must be > 0")
        return v


SPORT_MAPPING: dict[str, str] = {
    "run": "Run",
    "running": "Run",
    "ride": "Ride",
    "bike": "Ride",
    "cycling": "Ride",
    "swim": "Swim",
    "swimming": "Swim",
    "walk": "Walk",
    "rest": "Rest",
}


INTENSITY_MAPPING: dict[str, str] = {
    "easy": "easy",
    "easy run": "easy",
    "recovery": "easy",
    "moderate": "moderate",
    "tempo": "moderate",
    "threshold": "moderate",
    "hard": "hard",
    "interval": "hard",
    "intervals": "hard",
    "speed": "hard",
    "vo2": "hard",
    "race": "race",
    "race pace": "race",
}


def _parse_text_plan_line(line: str, base_date: datetime) -> ParsedSessionUpload | None:
    """Parse a single line from text plan.

    Args:
        line: Text line (e.g., "Mon easy 8, Tue intervals")
        base_date: Base date for relative dates

    Returns:
        Parsed session or None if line cannot be parsed
    """
    line_lower = line.lower().strip()
    if not line_lower:
        return None

    # Day abbreviations
    day_offsets: dict[str, int] = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }

    # Find day
    day_offset: int | None = None
    session_date = base_date
    for day_name, offset in day_offsets.items():
        if line_lower.startswith(day_name):
            day_offset = offset
            # Calculate date (find next occurrence of this day)
            days_ahead = (offset - base_date.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # Next week
            session_date = base_date + timedelta(days=days_ahead)
            session_date = session_date.replace(hour=0, minute=0, second=0, microsecond=0)
            break

    if day_offset is None:
        # No day found, use base_date
        session_date = base_date

    # Parse intensity
    intensity: str | None = None
    for key, val in INTENSITY_MAPPING.items():
        if key in line_lower:
            intensity = val
            break

    # Parse distance
    distance_km: float | None = None
    distance_patterns = [
        (r"(\d+\.?\d*)\s*miles?", "mile"),
        (r"(\d+\.?\d*)\s*mi\b", "mile"),
        (r"(\d+\.?\d*)\s*kilometers?", "km"),
        (r"(\d+\.?\d*)\s*km\b", "km"),
    ]
    for pattern, unit in distance_patterns:
        match = re.search(pattern, line_lower)
        if match:
            value = float(match.group(1))
            if unit == "mile":
                distance_km = value * 1.60934
            else:
                distance_km = value
            break

    # Parse sport
    sport = "Run"  # Default
    for key, val in SPORT_MAPPING.items():
        if key in line_lower:
            sport = val
            break

    # Generate title
    title_parts: list[str] = []
    if intensity:
        title_parts.append(intensity.capitalize())
    if distance_km:
        title_parts.append(f"{distance_km:.1f}km")
    title = " ".join(title_parts) if title_parts else sport

    # Parse duration (optional)
    duration_minutes: int | None = None
    duration_pattern = r"(\d+)\s*(?:min|minutes?)"
    match = re.search(duration_pattern, line_lower)
    if match:
        duration_minutes = int(match.group(1))

    return ParsedSessionUpload(
        date=session_date,
        type=sport,
        title=title,
        duration_minutes=duration_minutes,
        distance_km=distance_km,
        intensity=intensity or "easy",
        notes=line if len(line) > 50 else None,
    )


def _raise_no_sessions_error() -> None:
    """Raise error when no valid sessions found in CSV."""
    raise ValueError("No valid sessions found in CSV")


def parse_csv_plan(content: str, base_date: datetime | None = None) -> list[ParsedSessionUpload]:
    """Parse CSV content into list of sessions.

    Args:
        content: CSV file content as string
        base_date: Base date for relative dates (defaults to today)

    Returns:
        List of parsed sessions

    Raises:
        ValueError: If CSV parsing fails
    """
    if base_date is None:
        base_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        csv_reader = csv.DictReader(io.StringIO(content))
        sessions: list[ParsedSessionUpload] = []

        for row in csv_reader:
            try:
                row_lower = {k.lower(): v for k, v in row.items()}

                # Parse date
                date_str = row_lower.get("date") or row_lower.get("start_date") or ""
                if not date_str:
                    logger.warning(f"Row missing date: {row}")
                    continue

                try:
                    parsed_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                except ValueError:
                    try:
                        date_obj = date.fromisoformat(date_str)
                        parsed_date = datetime.combine(date_obj, datetime.min.time(), tzinfo=timezone.utc)
                    except ValueError:
                        logger.warning(f"Could not parse date: {date_str}")
                        parsed_date = base_date

                # Parse type/sport
                sport_str = row_lower.get("sport") or row_lower.get("type") or row_lower.get("workout") or "run"
                sport = SPORT_MAPPING.get(sport_str.lower(), "Run")

                # Parse title
                title = row_lower.get("title") or row_lower.get("workout") or row_lower.get("description") or sport

                # Parse distance
                distance_str = row_lower.get("distance") or row_lower.get("distance_km") or ""
                distance_km: float | None = None
                if distance_str:
                    with suppress(ValueError):
                        distance_km = float(distance_str)

                # Parse duration
                duration_str = row_lower.get("duration") or row_lower.get("duration_minutes") or ""
                duration_minutes: int | None = None
                if duration_str:
                    with suppress(ValueError):
                        duration_minutes = int(float(duration_str))

                # Parse intensity
                intensity_str = row_lower.get("intensity") or ""
                intensity: str | None = None
                if intensity_str:
                    intensity_lower = intensity_str.lower()
                    intensity = INTENSITY_MAPPING.get(intensity_lower, intensity_lower)

                # Parse time
                time_str = row_lower.get("time") or None

                # Parse week_number
                week_number_str = row_lower.get("week_number") or row_lower.get("week") or ""
                week_number: int | None = None
                if week_number_str:
                    with suppress(ValueError):
                        week_number = int(float(week_number_str))

                notes = row_lower.get("notes") or row_lower.get("description") or None

                session = ParsedSessionUpload(
                    date=parsed_date,
                    time=time_str,
                    type=sport,
                    title=title,
                    duration_minutes=duration_minutes,
                    distance_km=distance_km,
                    intensity=intensity,
                    notes=notes,
                    week_number=week_number,
                )
                sessions.append(session)
            except Exception as e:
                logger.warning(f"Error parsing CSV row: {e}: {row}")
                continue

        if not sessions:
            _raise_no_sessions_error()
        else:
            return sessions
    except Exception as e:
        raise ValueError(f"Failed to parse CSV: {e!s}") from e


def parse_text_plan(content: str, base_date: datetime | None = None) -> list[ParsedSessionUpload]:
    """Parse training plan from free text.

    Args:
        content: Free text description (e.g., "Mon easy 8, Tue intervals...")
        base_date: Base date for relative dates (defaults to today)

    Returns:
        List of parsed sessions

    Raises:
        ValueError: If parsing fails
    """
    if base_date is None:
        base_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    lines = content.split("\n")
    sessions: list[ParsedSessionUpload] = []

    for line in lines:
        parsed = _parse_text_plan_line(line, base_date)
        if parsed:
            sessions.append(parsed)

    if not sessions:
        raise ValueError("No valid sessions found in text")

    return sessions


def parse_plan_upload(content: str, base_date: datetime | None = None) -> list[ParsedSessionUpload]:
    """Parse training plan upload from CSV or text.

    Args:
        content: CSV content or free text
        base_date: Base date for relative dates (defaults to today)

    Returns:
        List of parsed sessions

    Raises:
        ValueError: If parsing fails
    """
    content_stripped = content.strip()
    if "\n" in content_stripped and "," in content_stripped:
        # Likely CSV
        try:
            return parse_csv_plan(content_stripped, base_date)
        except ValueError:
            logger.debug("CSV parsing failed, trying text parsing")
            pass

    # Try as free text
    return parse_text_plan(content_stripped, base_date)
