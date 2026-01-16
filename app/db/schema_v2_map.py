"""Schema v2 mapping utilities.

Conversion helpers for migrating between old and new schema field names.
Use these utilities during the migration to ensure consistent transformations.
"""

from __future__ import annotations

from datetime import datetime, time, timezone


def normalize_sport(sport: str) -> str:
    """Normalize sport string to schema v2 values.

    Schema v2 valid values: 'run', 'ride', 'swim', 'strength', 'walk', 'other'

    Args:
        sport: Sport name (case-insensitive, accepts common variations)

    Returns:
        Normalized sport string

    Examples:
        >>> normalize_sport('Running')
        'run'
        >>> normalize_sport('Cycling')
        'ride'
        >>> normalize_sport('WeightTraining')
        'strength'
        >>> normalize_sport('unknown')
        'other'
    """
    sport_lower = sport.lower().strip() if sport else ""

    mapping = {
        # Running variations
        "running": "run",
        "run": "run",
        # Cycling variations
        "cycling": "ride",
        "bike": "ride",
        "biking": "ride",
        "ride": "ride",
        # Swimming variations
        "swimming": "swim",
        "swim": "swim",
        # Strength variations
        "strength": "strength",
        "weighttraining": "strength",
        "weights": "strength",
        "crossfit": "strength",
        # Walking variations
        "walking": "walk",
        "walk": "walk",
        "hiking": "walk",
    }

    return mapping.get(sport_lower, "other")


def minutes_to_seconds(minutes: int | float | None) -> int | None:
    """Convert minutes to seconds.

    Args:
        minutes: Duration in minutes (nullable)

    Returns:
        Duration in seconds (nullable), or None if input is None

    Examples:
        >>> minutes_to_seconds(30)
        1800
        >>> minutes_to_seconds(1.5)
        90
        >>> minutes_to_seconds(None)
        None
    """
    if minutes is None:
        return None
    return int(minutes * 60)


def km_to_meters(km: float | None) -> float | None:
    """Convert kilometers to meters.

    Args:
        km: Distance in kilometers (nullable)

    Returns:
        Distance in meters (nullable), or None if input is None

    Examples:
        >>> km_to_meters(5.0)
        5000.0
        >>> km_to_meters(1.5)
        1500.0
        >>> km_to_meters(None)
        None
    """
    if km is None:
        return None
    return km * 1000.0


def mi_to_meters(miles: float | None) -> float | None:
    """Convert miles to meters.

    Args:
        miles: Distance in miles (nullable)

    Returns:
        Distance in meters (nullable), or None if input is None

    Examples:
        >>> mi_to_meters(3.1)
        4988.9664
        >>> mi_to_meters(1.0)
        1609.344
        >>> mi_to_meters(None)
        None
    """
    if miles is None:
        return None
    return miles * 1609.344


def seconds_to_minutes(seconds: int | None) -> int | None:
    """Convert seconds to minutes (for compatibility/readability).

    Args:
        seconds: Duration in seconds (nullable)

    Returns:
        Duration in minutes (nullable), rounded down, or None if input is None

    Examples:
        >>> seconds_to_minutes(1800)
        30
        >>> seconds_to_minutes(90)
        1
        >>> seconds_to_minutes(None)
        None
    """
    if seconds is None:
        return None
    return seconds // 60


def meters_to_km(meters: float | None) -> float | None:
    """Convert meters to kilometers (for compatibility/readability).

    Args:
        meters: Distance in meters (nullable)

    Returns:
        Distance in kilometers (nullable), or None if input is None

    Examples:
        >>> meters_to_km(5000.0)
        5.0
        >>> meters_to_km(1500.0)
        1.5
        >>> meters_to_km(None)
        None
    """
    if meters is None:
        return None
    return meters / 1000.0


def to_metrics(raw_json: dict | None = None, streams_data: dict | None = None, extra: dict | None = None) -> dict:
    """Build metrics dict from old field names.

    Schema v2: raw_json and streams_data are stored in the metrics JSONB field.

    Args:
        raw_json: Raw JSON data (typically from Strava API)
        streams_data: Time-series streams data (GPS, HR, power, etc.)
        extra: Additional metrics to include

    Returns:
        Metrics dict ready for Activity.metrics field

    Examples:
        >>> to_metrics(raw_json={'id': 123}, streams_data={'time': [1,2,3]})
        {'raw_json': {'id': 123}, 'streams_data': {'time': [1,2,3]}}

        >>> to_metrics(extra={'heartrate_avg': 150})
        {'heartrate_avg': 150}
    """
    metrics: dict = {}

    if raw_json is not None:
        metrics["raw_json"] = raw_json

    if streams_data is not None:
        metrics["streams_data"] = streams_data

    if extra is not None:
        metrics.update(extra)

    return metrics


def combine_date_time(date_value, time_value: str | None = None):
    """Combine date and time string into datetime.

    Helper for migrating PlannedSession.date + PlannedSession.time → PlannedSession.starts_at.

    Args:
        date_value: Date (datetime, date, or string)
        time_value: Time string in HH:MM format (optional)

    Returns:
        datetime object with timezone

    Examples:
        >>> from datetime import date, datetime
        >>> d = date(2024, 1, 15)
        >>> combine_date_time(d, "14:30")
        datetime.datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
    """
    # Handle date_value as datetime, date, or string
    if isinstance(date_value, datetime):
        dt = date_value
    elif isinstance(date_value, str):
        dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    else:
        # Assume it's a date object
        dt = datetime.combine(date_value, time.min).replace(tzinfo=timezone.utc)

    # Add time if provided
    if time_value:
        try:
            hour, minute = map(int, time_value.split(":"))
            dt = dt.replace(hour=hour, minute=minute)
        except (ValueError, AttributeError):
            pass  # Ignore invalid time format

    # Ensure timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt
