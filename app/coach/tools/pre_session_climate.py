"""Pre-session climate guidance tool.

Generates climate-aware guidance before a session using forecast data
and athlete baseline.
"""

from __future__ import annotations

from loguru import logger

from app.integrations.weather.client import WeatherClient, get_weather_client


def generate_pre_session_climate_guidance(
    session_type: str,
    forecast_temp_c: float,
    forecast_dew_point_c: float,
    athlete_climate_type: str | None = None,
) -> dict[str, str]:
    """Generate pre-session climate guidance.

    Args:
        session_type: Session type ('long_run', 'tempo', 'easy', etc.)
        planned_pace: Planned pace (e.g., "4:15/km")
        forecast_temp_c: Forecast temperature in Celsius
        forecast_dew_point_c: Forecast dew point in Celsius
        athlete_climate_type: Athlete's baseline climate type (optional)

    Returns:
        Dictionary with:
        - guidance: Human-readable guidance text
    """
    # Classify forecast conditions
    if forecast_temp_c < 15.0 or forecast_dew_point_c < 10.0:
        conditions = "cool"
    elif forecast_temp_c < 25.0 and forecast_dew_point_c < 18.0:
        conditions = "warm"
    elif forecast_temp_c < 30.0 and forecast_dew_point_c < 22.0:
        conditions = "hot"
    elif forecast_temp_c < 35.0 and forecast_dew_point_c < 25.0:
        conditions = "hot_humid"
    else:
        conditions = "extreme"

    # Compare to athlete baseline
    baseline_comparison = ""
    if athlete_climate_type:
        if athlete_climate_type == "Temperate" and conditions in {"hot", "hot_humid", "extreme"}:
            baseline_comparison = "Conditions are hotter than your normal baseline. "
        elif athlete_climate_type == "Hot" and conditions in {"cool", "warm"}:
            baseline_comparison = "Conditions are cooler than your normal baseline. "
        elif athlete_climate_type == "Cool" and conditions in {"hot", "hot_humid", "extreme"}:
            baseline_comparison = "Conditions are significantly hotter than your normal baseline. "

    # Generate guidance based on conditions and session type
    guidance_parts = []

    if conditions == "cool":
        guidance_parts.append("Conditions are cool—good for performance.")
        if session_type in {"long_run", "tempo"}:
            guidance_parts.append("You may be able to push slightly harder than planned.")
    elif conditions == "warm":
        guidance_parts.append("Conditions are warm but manageable.")
        if session_type == "long_run":
            guidance_parts.append("Stay hydrated and monitor your effort.")
    elif conditions == "hot":
        guidance_parts.append("Conditions are hot.")
        if session_type == "long_run":
            guidance_parts.append("Start conservatively and hydrate early.")
        elif session_type == "tempo":
            guidance_parts.append("Consider adjusting pace targets downward by 5-10%.")
    elif conditions == "hot_humid":
        guidance_parts.append("Conditions are hot and humid—significant heat stress expected.")
        if session_type == "long_run":
            guidance_parts.append("Start conservatively, hydrate frequently, and be prepared to adjust pace.")
        elif session_type == "tempo":
            guidance_parts.append("Consider reducing intensity or moving to cooler time of day.")
    else:  # extreme
        guidance_parts.append("Extreme heat conditions—high risk of heat stress.")
        if session_type in {"long_run", "tempo"}:
            guidance_parts.append("Strongly consider rescheduling or significantly reducing intensity.")
        else:
            guidance_parts.append("Consider rescheduling to a cooler time.")

    # Add baseline comparison if available
    if baseline_comparison:
        guidance_parts.insert(0, baseline_comparison)

    guidance = " ".join(guidance_parts)

    logger.info(
        f"[CLIMATE] Pre-session guidance: session_type={session_type}, "
        f"temp={forecast_temp_c:.1f}°C, dew_point={forecast_dew_point_c:.1f}°C, "
        f"conditions={conditions}, baseline={athlete_climate_type or 'unknown'}"
    )

    return {"guidance": guidance}
