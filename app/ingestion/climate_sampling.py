"""Climate sampling during activity ingestion.

Samples weather data every 15 minutes for activities with GPS data.
Stores raw samples in activity_climate_samples table.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import Activity
from app.integrations.weather.client import WeatherClient, get_weather_client


def sample_activity_climate(
    session: Session,
    activity: Activity,
    weather_client: WeatherClient | None = None,
) -> int:
    """Sample climate data for an activity with GPS.

    Samples weather every 15 minutes using midpoint lat/lon for each interval.
    Stores raw samples in activity_climate_samples table.

    Args:
        session: Database session
        activity: Activity record (must have streams_data with GPS)
        weather_client: Weather client instance (optional, will create if not provided)

    Returns:
        Number of samples successfully stored
    """
    if weather_client is None:
        weather_client = get_weather_client()

    if not weather_client.api_key:
        logger.debug(f"[CLIMATE] Skipping climate sampling for activity {activity.id}: API key not configured")
        return 0

    # Extract GPS data from streams
    streams_data = activity.metrics.get("streams_data") if activity.metrics else None
    if not streams_data:
        logger.debug(f"[CLIMATE] Activity {activity.id} has no streams_data, skipping climate sampling")
        return 0

    # Extract lat/lon from streams
    # Strava streams format: {"latlng": {"data": [[lat, lon], ...]}}
    latlng_data = None
    if "latlng" in streams_data:
        latlng_stream = streams_data["latlng"]
        if isinstance(latlng_stream, dict) and "data" in latlng_stream:
            latlng_data = latlng_stream["data"]
        elif isinstance(latlng_stream, list):
            latlng_data = latlng_stream

    if not latlng_data or len(latlng_data) == 0:
        logger.debug(f"[CLIMATE] Activity {activity.id} has no GPS data, skipping climate sampling")
        return 0

    # Extract time series
    time_data = None
    if "time" in streams_data:
        time_stream = streams_data["time"]
        if isinstance(time_stream, dict) and "data" in time_stream:
            time_data = time_stream["data"]
        elif isinstance(time_stream, list):
            time_data = time_stream

    if not time_data or len(time_data) == 0:
        logger.debug(f"[CLIMATE] Activity {activity.id} has no time data, skipping climate sampling")
        return 0

    # Ensure time and latlng arrays have same length
    min_length = min(len(latlng_data), len(time_data))
    if min_length == 0:
        logger.debug(f"[CLIMATE] Activity {activity.id} has empty GPS/time arrays, skipping")
        return 0

    # Calculate sample intervals (every 15 minutes)
    start_time = activity.starts_at
    duration_seconds = activity.duration_seconds
    sample_interval_seconds = 15 * 60  # 15 minutes

    samples_stored = 0
    sample_times = []

    # Generate sample times
    current_time = start_time
    while current_time <= start_time + timedelta(seconds=duration_seconds):
        sample_times.append(current_time)
        current_time += timedelta(seconds=sample_interval_seconds)

    if not sample_times:
        logger.debug(f"[CLIMATE] No sample times generated for activity {activity.id}")
        return 0

    logger.debug(f"[CLIMATE] Sampling climate for activity {activity.id}: {len(sample_times)} intervals")

    # For each sample time, find the closest GPS point and sample weather
    for sample_time in sample_times:
        # Find closest time index
        elapsed_seconds = (sample_time - start_time).total_seconds()
        time_index = None

        # Find the index in time_data closest to elapsed_seconds
        closest_diff = float("inf")
        for i, time_val in enumerate(time_data[:min_length]):
            if time_val is None:
                continue
            try:
                time_float = float(time_val)
                diff = abs(time_float - elapsed_seconds)
                if diff < closest_diff:
                    closest_diff = diff
                    time_index = i
            except (ValueError, TypeError):
                continue

        if time_index is None or time_index >= len(latlng_data):
            logger.debug(f"[CLIMATE] No GPS point found for sample time {sample_time}")
            continue

        # Get lat/lon for this sample
        latlng_point = latlng_data[time_index]
        if not latlng_point or len(latlng_point) < 2:
            continue

        try:
            lat = float(latlng_point[0])
            lon = float(latlng_point[1])
        except (ValueError, TypeError, IndexError):
            logger.debug(f"[CLIMATE] Invalid GPS point at index {time_index}: {latlng_point}")
            continue

        # Fetch historical weather
        weather_data = weather_client.fetch_historical_weather(lat, lon, sample_time)
        if not weather_data:
            logger.debug(f"[CLIMATE] Failed to fetch weather for {lat}, {lon} at {sample_time}")
            continue

        # Store sample in database
        try:
            sample_id = str(uuid.uuid4())
            session.execute(
                text(
                    """
                    INSERT INTO activity_climate_samples (
                        id, activity_id, sample_time, lat, lon,
                        temperature_c, humidity_pct, dew_point_c,
                        wind_speed_mps, wind_direction_deg, precip_mm, source
                    ) VALUES (
                        :id, :activity_id, :sample_time, :lat, :lon,
                        :temperature_c, :humidity_pct, :dew_point_c,
                        :wind_speed_mps, :wind_direction_deg, :precip_mm, :source
                    )
                    """
                ),
                {
                    "id": sample_id,
                    "activity_id": activity.id,
                    "sample_time": sample_time,
                    "lat": lat,
                    "lon": lon,
                    "temperature_c": weather_data.get("temperature_c"),
                    "humidity_pct": weather_data.get("humidity_pct"),
                    "dew_point_c": weather_data.get("dew_point_c"),
                    "wind_speed_mps": weather_data.get("wind_speed_mps"),
                    "wind_direction_deg": weather_data.get("wind_direction_deg"),
                    "precip_mm": weather_data.get("precip_mm"),
                    "source": weather_data.get("source", "openweathermap"),
                },
            )
            samples_stored += 1
        except Exception as e:
            logger.warning(f"[CLIMATE] Failed to store climate sample for activity {activity.id} at {sample_time}: {e}")
            continue

    logger.info(f"[CLIMATE] Stored {samples_stored}/{len(sample_times)} climate samples for activity {activity.id}")
    return samples_stored
