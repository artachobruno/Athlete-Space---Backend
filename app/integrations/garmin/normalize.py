"""Normalization layer for Garmin activity data.

Converts Garmin API payloads to ActivityCreate format.
Pure mapper function with no side effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger


def normalize_garmin_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Garmin activity payload to ActivityCreate format.

    Pure mapper function:
    - sport
    - start_time
    - duration
    - distance
    - calories
    - avg/max HR
    - laps

    Args:
        payload: Raw Garmin activity payload

    Returns:
        Normalized activity dict compatible with ActivityCreate schema
    """
    logger.debug("Normalizing Garmin activity payload")

    # Extract basic fields (adjust based on actual Garmin API format)
    activity_id = str(payload.get("activityId") or payload.get("activity_id") or "")
    sport_type = payload.get("activityType") or payload.get("activity_type") or payload.get("sportType") or "other"

    # Map Garmin sport types to our sport enum
    sport_mapping: dict[str, str] = {
        "running": "run",
        "cycling": "ride",
        "swimming": "swim",
        "strength_training": "strength",
        "walking": "walk",
    }
    sport = sport_mapping.get(sport_type.lower(), "other")

    # Extract timestamps
    start_time_str = payload.get("startTimeGMT") or payload.get("start_time_gmt") or payload.get("startTime")
    if start_time_str:
        # Parse Garmin timestamp format (adjust as needed)
        try:
            if isinstance(start_time_str, str):
                # Try ISO format first
                start_time = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            else:
                start_time = datetime.fromtimestamp(start_time_str, tz=timezone.utc)
        except Exception:
            logger.warning(f"Failed to parse start_time: {start_time_str}, using current time")
            start_time = datetime.now(timezone.utc)
    else:
        start_time = datetime.now(timezone.utc)

    # Extract duration (seconds)
    duration_seconds = int(payload.get("duration") or payload.get("elapsedDuration") or payload.get("elapsed_duration") or 0)

    # Extract distance (meters)
    distance_meters: float | None = None
    if "distance" in payload:
        distance_meters = float(payload["distance"])
    elif "distanceInMeters" in payload:
        distance_meters = float(payload["distanceInMeters"])

    # Extract elevation (meters)
    elevation_gain_meters: float | None = None
    if "elevationGain" in payload:
        elevation_gain_meters = float(payload["elevationGain"])
    elif "totalElevationGain" in payload:
        elevation_gain_meters = float(payload["totalElevationGain"])

    # Extract calories
    calories: float | None = None
    if "calories" in payload:
        calories = float(payload["calories"])

    # Extract HR data
    avg_hr: int | None = None
    max_hr: int | None = None
    if "averageHR" in payload:
        avg_hr = int(payload["averageHR"])
    elif "avgHeartRate" in payload:
        avg_hr = int(payload["avgHeartRate"])

    if "maxHR" in payload:
        max_hr = int(payload["maxHR"])
    elif "maxHeartRate" in payload:
        max_hr = int(payload["maxHeartRate"])

    # Extract laps (if available)
    laps: list[dict[str, Any]] = []
    if "laps" in payload and isinstance(payload["laps"], list):
        laps = payload["laps"]

    # Calculate end time
    ends_at = start_time.replace(microsecond=0) if duration_seconds else None
    if ends_at and duration_seconds:
        from datetime import timedelta

        ends_at = ends_at + timedelta(seconds=duration_seconds)

    # Build metrics dict
    metrics: dict[str, Any] = {
        "raw_json": payload,
    }

    if avg_hr or max_hr:
        metrics["heart_rate"] = {}
        if avg_hr:
            metrics["heart_rate"]["avg"] = avg_hr
        if max_hr:
            metrics["heart_rate"]["max"] = max_hr

    if laps:
        metrics["laps"] = laps

    # Build normalized activity
    normalized: dict[str, Any] = {
        "sport": sport,
        "start_time": start_time.isoformat(),
        "duration_seconds": duration_seconds,
        "source": "garmin",
        "source_provider": "garmin",
        "external_activity_id": activity_id,
        "source_activity_id": activity_id,
    }

    if distance_meters is not None:
        normalized["distance_meters"] = distance_meters

    if elevation_gain_meters is not None:
        normalized["elevation_gain_meters"] = elevation_gain_meters

    if calories is not None:
        normalized["calories"] = calories

    if ends_at:
        normalized["ends_at"] = ends_at.isoformat()

    normalized["metrics"] = metrics

    # Add title if available
    if "activityName" in payload:
        normalized["title"] = payload["activityName"]
    elif "name" in payload:
        normalized["title"] = payload["name"]

    logger.debug(f"Normalized Garmin activity: sport={sport}, duration={duration_seconds}s")
    return normalized
