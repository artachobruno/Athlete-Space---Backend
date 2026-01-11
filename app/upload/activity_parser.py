"""Parser for activity uploads from CSV or free text.

Handles parsing activities from:
- CSV files (Strava-like or generic format)
- Free text descriptions ("Ran 10 miles in 1:05 with intervals")
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


class ParsedActivityUpload(BaseModel):
    """Parsed activity data from upload."""

    start_time: datetime
    duration_seconds: int
    distance_meters: float
    sport: str
    date: datetime
    avg_hr: int | None = None
    notes: str | None = None
    elevation_gain_meters: float | None = None

    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        """Validate duration is positive."""
        if v <= 0:
            raise ValueError("duration_seconds must be > 0")
        return v

    @field_validator("distance_meters")
    @classmethod
    def validate_distance(cls, v: float) -> float:
        """Validate distance is positive."""
        if v <= 0:
            raise ValueError("distance_meters must be > 0")
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
    "walking": "Walk",
}


def _parse_distance(text: str) -> float | None:
    """Parse distance from text (miles or kilometers).

    Args:
        text: Text containing distance

    Returns:
        Distance in meters or None if not found
    """
    # Match patterns like "10 miles", "10mi", "16 km", "16km", "10.5 miles"
    patterns = [
        (r"(\d+\.?\d*)\s*miles?", "mile"),
        (r"(\d+\.?\d*)\s*mi\b", "mile"),
        (r"(\d+\.?\d*)\s*kilometers?", "km"),
        (r"(\d+\.?\d*)\s*km\b", "km"),
    ]

    for pattern, unit in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if unit == "mile":
                return value * 1609.34  # Convert miles to meters
            return value * 1000  # Convert km to meters
    return None


def _parse_duration(text: str) -> int | None:
    """Parse duration from text (hours:minutes:seconds or minutes).

    Args:
        text: Text containing duration

    Returns:
        Duration in seconds or None if not found
    """
    # Match HH:MM:SS or H:MM:SS
    time_pattern = r"(\d{1,2}):(\d{2}):(\d{2})"
    match = re.search(time_pattern, text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        return hours * 3600 + minutes * 60 + seconds

    # Match HH:MM or H:MM
    time_pattern = r"(\d{1,2}):(\d{2})(?!\d)"
    match = re.search(time_pattern, text)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        return hours * 3600 + minutes * 60

    # Match "X minutes" or "X mins"
    minute_pattern = r"(\d+)\s*(?:minutes?|mins?)"
    match = re.search(minute_pattern, text, re.IGNORECASE)
    if match:
        return int(match.group(1)) * 60

    return None


def _parse_sport(text: str) -> str:
    """Parse sport type from text.

    Args:
        text: Text containing sport description

    Returns:
        Normalized sport name (Run, Ride, Swim, etc.)
    """
    text_lower = text.lower()
    for key, sport in SPORT_MAPPING.items():
        if key in text_lower:
            return sport
    return "Run"  # Default to Run


def _parse_date(text: str) -> datetime | None:
    """Parse date from text (relative or absolute).

    Args:
        text: Text containing date information

    Returns:
        Datetime or None if not found
    """
    now = datetime.now(timezone.utc)
    text_lower = text.lower()

    # Relative dates
    if "yesterday" in text_lower:
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in text_lower:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Try ISO format dates
    iso_pattern = r"\d{4}-\d{2}-\d{2}"
    match = re.search(iso_pattern, text)
    if match:
        try:
            date_str = match.group(0)
            date_obj = date.fromisoformat(date_str)
        except ValueError:
            pass
        else:
            return datetime.combine(date_obj, datetime.min.time(), tzinfo=timezone.utc)

    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_heart_rate(text: str) -> int | None:
    """Parse heart rate from text.

    Args:
        text: Text containing heart rate

    Returns:
        Heart rate in bpm or None if not found
    """
    hr_pattern = r"(\d+)\s*(?:bpm|hr|heart\s*rate)"
    match = re.search(hr_pattern, text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _raise_no_activities_error() -> None:
    """Raise error when no valid activities found in CSV."""
    raise ValueError("No valid activities found in CSV")


def parse_csv_activity(content: str) -> list[ParsedActivityUpload]:
    """Parse CSV content into list of activities.

    Args:
        content: CSV file content as string

    Returns:
        List of parsed activities

    Raises:
        ValueError: If CSV parsing fails
    """
    try:
        csv_reader = csv.DictReader(io.StringIO(content))
        activities: list[ParsedActivityUpload] = []

        for row in csv_reader:
            try:
                # Try to extract fields (case-insensitive matching)
                row_lower = {k.lower(): v for k, v in row.items()}

                # Parse date
                date_str = (
                    row_lower.get("date")
                    or row_lower.get("start_time")
                    or row_lower.get("time")
                    or ""
                )
                if not date_str:
                    logger.warning(f"Row missing date: {row}")
                    continue

                try:
                    parsed_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                except ValueError:
                    # Try other formats
                    try:
                        date_obj = date.fromisoformat(date_str)
                        parsed_date = datetime.combine(date_obj, datetime.min.time(), tzinfo=timezone.utc)
                    except ValueError:
                        logger.warning(f"Could not parse date: {date_str}")
                        parsed_date = datetime.now(timezone.utc)

                # Parse distance
                distance_str = row_lower.get("distance") or row_lower.get("distance_m") or ""
                if not distance_str:
                    logger.warning(f"Row missing distance: {row}")
                    continue

                try:
                    distance_m = float(distance_str)
                except ValueError:
                    logger.warning(f"Could not parse distance: {distance_str}")
                    continue

                # Parse duration
                duration_str = row_lower.get("duration") or row_lower.get("duration_seconds") or ""
                if not duration_str:
                    logger.warning(f"Row missing duration: {row}")
                    continue

                try:
                    duration_seconds = int(float(duration_str))
                except ValueError:
                    logger.warning(f"Could not parse duration: {duration_str}")
                    continue

                # Parse sport
                sport_str = (
                    row_lower.get("sport")
                    or row_lower.get("type")
                    or row_lower.get("activity_type")
                    or "run"
                )
                sport = SPORT_MAPPING.get(sport_str.lower(), "Run")

                # Parse optional fields
                hr_str = row_lower.get("heart_rate") or row_lower.get("hr") or row_lower.get("avg_hr") or ""
                avg_hr: int | None = None
                if hr_str:
                    with suppress(ValueError):
                        avg_hr = int(float(hr_str))

                elevation_str = row_lower.get("elevation") or row_lower.get("elevation_gain") or ""
                elevation: float | None = None
                if elevation_str:
                    with suppress(ValueError):
                        elevation = float(elevation_str)

                notes = row_lower.get("notes") or row_lower.get("description") or None

                activity = ParsedActivityUpload(
                    start_time=parsed_date,
                    date=parsed_date,
                    duration_seconds=duration_seconds,
                    distance_meters=distance_m,
                    sport=sport,
                    avg_hr=avg_hr,
                    elevation_gain_meters=elevation,
                    notes=notes,
                )
                activities.append(activity)
            except Exception as e:
                logger.warning(f"Error parsing CSV row: {e}: {row}")
                continue

        if not activities:
            _raise_no_activities_error()
        else:
            return activities
    except Exception as e:
        raise ValueError(f"Failed to parse CSV: {e!s}") from e


def parse_text_activity(text: str) -> ParsedActivityUpload:
    """Parse activity from free text description.

    Args:
        text: Free text description (e.g., "Ran 10 miles in 1:05 with intervals")

    Returns:
        Parsed activity

    Raises:
        ValueError: If required fields cannot be parsed
    """
    # Parse distance
    distance_m = _parse_distance(text)
    if distance_m is None:
        raise ValueError("Could not parse distance from text")

    # Parse duration
    duration_seconds = _parse_duration(text)
    if duration_seconds is None:
        raise ValueError("Could not parse duration from text")

    # Parse sport
    sport = _parse_sport(text)

    # Parse date (defaults to today if not found)
    parsed_date = _parse_date(text)
    if parsed_date is None:
        parsed_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Parse optional fields
    avg_hr = _parse_heart_rate(text)
    notes = text if len(text) > 100 else None

    return ParsedActivityUpload(
        start_time=parsed_date,
        date=parsed_date,
        duration_seconds=duration_seconds,
        distance_meters=distance_m,
        sport=sport,
        avg_hr=avg_hr,
        notes=notes,
    )


def parse_activity_upload(content: str) -> list[ParsedActivityUpload]:
    """Parse activity upload from CSV or text.

    Args:
        content: CSV content or free text

    Returns:
        List of parsed activities (single item for text, multiple for CSV)

    Raises:
        ValueError: If parsing fails
    """
    # Check if content looks like CSV (has headers or comma-separated values)
    content_stripped = content.strip()
    if "\n" in content_stripped and "," in content_stripped:
        # Likely CSV
        try:
            return parse_csv_activity(content_stripped)
        except ValueError:
            # Fall through to text parsing
            logger.debug("CSV parsing failed, trying text parsing")
            pass

    # Try as free text
    try:
        activity = parse_text_activity(content_stripped)
    except ValueError as e:
        raise ValueError(f"Failed to parse activity upload: {e!s}") from e
    else:
        return [activity]
