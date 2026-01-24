"""Weather API client for fetching historical weather data.

This module provides a client for fetching historical weather data
for climate sampling during activity ingestion.

Currently uses OpenWeatherMap API as the weather provider.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from loguru import logger

from app.config.settings import settings


class WeatherClient:
    """Client for fetching historical weather data."""

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize weather client.

        Args:
            api_key: OpenWeatherMap API key. If not provided, reads from settings.
        """
        self.api_key = api_key or getattr(settings, "openweather_api_key", None)
        if not self.api_key:
            logger.warning(
                "OpenWeatherMap API key not configured. Climate sampling will be disabled. "
                "Set OPENWEATHER_API_KEY environment variable to enable."
            )
        self.base_url = "https://api.openweathermap.org/data/3.0/onecall/timemachine"

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

            Returns None if API call fails or API key is not configured.
        """
        if not self.api_key:
            logger.debug("Weather API key not configured, skipping weather fetch")
            return None

        try:
            # OpenWeatherMap requires Unix timestamp
            unix_timestamp = int(timestamp.timestamp())

            # Make API request
            url = f"{self.base_url}"
            params = {
                "lat": lat,
                "lon": lon,
                "dt": unix_timestamp,
                "appid": self.api_key,
                "units": "metric",  # Get temperature in Celsius
            }

            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            # Extract weather data from response
            # OpenWeatherMap One Call API structure
            result = None
            if "data" in data and len(data["data"]) > 0:
                current = data["data"][0]

                # Temperature (already in Celsius due to units=metric)
                temp_c = current.get("temp")
                # Humidity percentage
                humidity_pct = current.get("humidity")
                # Dew point (already in Celsius)
                dew_point_c = current.get("dew_point")
                # Wind speed (m/s)
                wind_speed_mps = current.get("wind_speed")
                # Wind direction (degrees)
                wind_direction_deg = current.get("wind_deg")
                # Precipitation (mm) - may not be available for all timestamps
                precip_mm = current.get("rain", {}).get("1h") if "rain" in current else None
                if precip_mm is None:
                    precip_mm = current.get("precipitation", 0.0)

                result = {
                    "temperature_c": float(temp_c) if temp_c is not None else None,
                    "humidity_pct": float(humidity_pct) if humidity_pct is not None else None,
                    "dew_point_c": float(dew_point_c) if dew_point_c is not None else None,
                    "wind_speed_mps": float(wind_speed_mps) if wind_speed_mps is not None else None,
                    "wind_direction_deg": float(wind_direction_deg) if wind_direction_deg is not None else None,
                    "precip_mm": float(precip_mm) if precip_mm is not None else 0.0,
                    "source": "openweathermap",
                }
            else:
                logger.warning(f"No weather data in response for {lat}, {lon} at {timestamp}")

        except httpx.HTTPStatusError as e:
            logger.warning(f"Weather API HTTP error for {lat}, {lon} at {timestamp}: {e.response.status_code}")
            return None
        except httpx.RequestError as e:
            logger.warning(f"Weather API request error for {lat}, {lon} at {timestamp}: {e}")
            return None
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Error parsing weather API response for {lat}, {lon} at {timestamp}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error fetching weather for {lat}, {lon} at {timestamp}: {e}")
            return None
        else:
            return result


def get_weather_client() -> WeatherClient:
    """Get a configured weather client instance."""
    api_key = getattr(settings, "openweather_api_key", None)
    return WeatherClient(api_key=api_key)
