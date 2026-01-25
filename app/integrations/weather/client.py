"""Weather API client for fetching historical weather data.

This module provides a client for fetching historical weather data
for climate sampling during activity ingestion.

Providers:
- OpenWeatherMap One Call 3.0 Timemachine (when OPENWEATHER_API_KEY is set)
- Open-Meteo Archive (free fallback, no API key; ~5-day delay for recent data)
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from loguru import logger

from app.config.settings import settings

_OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_OWM_TIMEMACHINE_URL = "https://api.openweathermap.org/data/3.0/onecall/timemachine"
_KMH_TO_MPS = 1.0 / 3.6


def _fetch_openmeteo(
    lat: float,
    lon: float,
    timestamp: datetime,
) -> dict[str, float | str | None] | None:
    """Fetch historical weather from Open-Meteo (free, no API key).

    Uses Archive API. ~5-day delay for most recent data.

    Returns:
        Same shape as WeatherClient.fetch_historical_weather, or None on failure.
    """
    try:
        date_str = timestamp.strftime("%Y-%m-%d")
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": date_str,
            "end_date": date_str,
            "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,wind_speed_10m,wind_direction_10m,precipitation",
            "timezone": "UTC",
        }
        with httpx.Client(timeout=10.0) as client:
            response = client.get(_OPENMETEO_ARCHIVE_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError, TypeError) as e:
        logger.debug("Open-Meteo archive fetch failed for %s, %s at %s: %s", lat, lon, timestamp, e)
        return None

    hourly = data.get("hourly")
    if not hourly or not isinstance(hourly, dict):
        return None

    times = hourly.get("time")
    if not times or not isinstance(times, list):
        return None

    ts_utc = timestamp.astimezone(timezone.utc) if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    idx = min(ts_utc.hour, len(times) - 1) if times else 0

    def _at(name: str, default: float | None = None) -> float | None:
        arr = hourly.get(name)
        if not isinstance(arr, list) or idx >= len(arr):
            return default
        v = arr[idx]
        return float(v) if v is not None else default

    temp_c = _at("temperature_2m")
    humidity_pct = _at("relative_humidity_2m")
    dew_point_c = _at("dew_point_2m")
    wind_kmh = _at("wind_speed_10m")
    wind_speed_mps = (wind_kmh * _KMH_TO_MPS) if wind_kmh is not None else None
    wind_direction_deg = _at("wind_direction_10m")
    precip_mm = _at("precipitation", 0.0) or 0.0

    return {
        "temperature_c": temp_c,
        "humidity_pct": humidity_pct,
        "dew_point_c": dew_point_c,
        "wind_speed_mps": wind_speed_mps,
        "wind_direction_deg": wind_direction_deg,
        "precip_mm": precip_mm,
        "source": "openmeteo",
    }


class WeatherClient:
    """Client for fetching historical weather data.

    Uses OpenWeatherMap when OPENWEATHER_API_KEY is set; otherwise
    Open-Meteo (free, no key). Always available for climate sampling.
    """

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize weather client.

        Args:
            api_key: OpenWeatherMap API key. If not provided, reads from settings.
                     When empty, Open-Meteo (free) is used.
        """
        self.api_key = (api_key or getattr(settings, "openweather_api_key", None) or "").strip()
        self._use_openmeteo = not bool(self.api_key)
        if self._use_openmeteo:
            logger.info(
                "Using Open-Meteo (free) for climate data. "
                "Set OPENWEATHER_API_KEY to use OpenWeatherMap instead."
            )

    @staticmethod
    def is_available() -> bool:
        """Return True if weather fetch is available (OWM or Open-Meteo)."""
        return True

    def fetch_historical_weather(
        self,
        lat: float,
        lon: float,
        timestamp: datetime,
    ) -> dict[str, float | str | None] | None:
        """Fetch historical weather data for a specific location and time.

        Args:
            lat: Latitude
            lon: Longitude
            timestamp: Timestamp for historical weather (must be in the past)

        Returns:
            Dictionary with weather data:
            - temperature_c: Temperature in Celsius
            - humidity_pct: Humidity percentage
            - dew_point_c: Dew point in Celsius
            - wind_speed_mps: Wind speed in meters per second
            - wind_direction_deg: Wind direction in degrees
            - precip_mm: Precipitation in millimeters
            - source: Data source identifier

            Returns None if API call fails.
        """
        if self._use_openmeteo:
            return _fetch_openmeteo(lat, lon, timestamp)

        try:
            unix_ts = int(timestamp.timestamp())
            params = {
                "lat": lat,
                "lon": lon,
                "dt": unix_ts,
                "appid": self.api_key,
                "units": "metric",
            }
            with httpx.Client(timeout=10.0) as client:
                response = client.get(_OWM_TIMEMACHINE_URL, params=params)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.warning("OpenWeatherMap request failed for %s, %s at %s: %s", lat, lon, timestamp, e)
            return None

        if "data" not in data or not data["data"]:
            logger.warning("No weather data in OWM response for %s, %s at %s", lat, lon, timestamp)
            return None

        current = data["data"][0]
        temp_c = current.get("temp")
        humidity_pct = current.get("humidity")
        dew_point_c = current.get("dew_point")
        wind_speed_mps = current.get("wind_speed")
        wind_direction_deg = current.get("wind_deg")
        precip_mm = current.get("rain", {}).get("1h") if isinstance(current.get("rain"), dict) else None
        if precip_mm is None:
            precip_mm = current.get("precipitation", 0.0)

        return {
            "temperature_c": float(temp_c) if temp_c is not None else None,
            "humidity_pct": float(humidity_pct) if humidity_pct is not None else None,
            "dew_point_c": float(dew_point_c) if dew_point_c is not None else None,
            "wind_speed_mps": float(wind_speed_mps) if wind_speed_mps is not None else None,
            "wind_direction_deg": float(wind_direction_deg) if wind_direction_deg is not None else None,
            "precip_mm": float(precip_mm) if precip_mm is not None else 0.0,
            "source": "openweathermap",
        }


def get_weather_client() -> WeatherClient:
    """Get a configured weather client instance."""
    api_key = getattr(settings, "openweather_api_key", None)
    return WeatherClient(api_key=api_key)
