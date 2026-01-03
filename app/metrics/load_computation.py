"""Load computation engine for training metrics.

Step 6: Computes per-activity load and aggregates to daily/weekly metrics.
Deterministic, explainable, and reproducible.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.state.models import Activity


def compute_activity_load(activity: Activity) -> float:  # noqa: C901, PLR0912
    """Compute training load for a single activity.

    Uses TRIMP (Training Impulse) proxy based on duration and intensity.
    For activities without HR data, uses duration-based estimation.

    Args:
        activity: Activity record

    Returns:
        Load score (float, typically 0-100+ depending on duration/intensity)

    Formula:
        - Base load = duration_hours * intensity_factor
        - Intensity factor based on activity type and distance
        - For runs: higher intensity for shorter/faster runs
        - For rides: power-based if available, otherwise duration-based
    """
    duration_hours = activity.duration_seconds / 3600.0

    # Base load from duration
    base_load = duration_hours

    # Intensity multiplier based on activity type
    activity_type = activity.type.lower()

    # Extract raw data for intensity estimation
    raw_data = activity.raw_json or {}
    avg_hr = raw_data.get("average_heartrate")
    avg_power = raw_data.get("average_watts")
    max_hr = raw_data.get("max_heartrate")

    # Calculate intensity factor
    intensity_factor = 1.0

    if activity_type in {"run", "trail run", "walk"}:
        # Running: estimate intensity from pace (distance/duration)
        if activity.distance_meters > 0 and duration_hours > 0:
            pace_kmh = (activity.distance_meters / 1000.0) / duration_hours
            # Faster pace = higher intensity
            # Typical easy pace: 8-10 km/h, tempo: 12-14 km/h, threshold: 15+ km/h
            if pace_kmh < 8:
                intensity_factor = 0.8  # Very easy/recovery
            elif pace_kmh < 10:
                intensity_factor = 1.0  # Easy
            elif pace_kmh < 12:
                intensity_factor = 1.2  # Moderate
            elif pace_kmh < 15:
                intensity_factor = 1.5  # Tempo
            else:
                intensity_factor = 2.0  # Threshold/VO2max

        # HR-based adjustment if available
        if avg_hr and max_hr:
            hr_ratio = avg_hr / max_hr if max_hr > 0 else 0.5
            # HR zones: 0.5-0.6 (zone 1), 0.6-0.7 (zone 2), 0.7-0.8 (zone 3), 0.8-0.9 (zone 4), 0.9+ (zone 5)
            if hr_ratio < 0.6:
                intensity_factor *= 0.8
            elif hr_ratio < 0.7:
                intensity_factor *= 1.0
            elif hr_ratio < 0.8:
                intensity_factor *= 1.3
            elif hr_ratio < 0.9:
                intensity_factor *= 1.6
            else:
                intensity_factor *= 2.0

    elif activity_type in {"ride", "virtualride", "ebikeride"}:
        # Cycling: power-based if available, otherwise duration-based
        if avg_power:
            # Power-based load: TSS proxy
            # Typical FTP: 200-300W, so normalize around 250W
            normalized_power = avg_power / 250.0
            intensity_factor = max(0.5, min(2.0, normalized_power))
        # Duration-based estimation
        # Longer rides typically lower intensity
        elif duration_hours > 4:
            intensity_factor = 0.8  # Endurance pace
        elif duration_hours > 2:
            intensity_factor = 1.0  # Moderate
        else:
            intensity_factor = 1.3  # Shorter = likely higher intensity

        # HR-based adjustment if available
        if avg_hr and max_hr:
            hr_ratio = avg_hr / max_hr if max_hr > 0 else 0.5
            if hr_ratio < 0.6:
                intensity_factor *= 0.8
            elif hr_ratio < 0.7:
                intensity_factor *= 1.0
            elif hr_ratio < 0.8:
                intensity_factor *= 1.2
            elif hr_ratio < 0.9:
                intensity_factor *= 1.5
            else:
                intensity_factor *= 1.8

    elif activity_type in {"swim"}:
        # Swimming: typically lower intensity multiplier
        intensity_factor = 0.7  # Swimming is typically lower impact

    else:
        # Other activities: duration-based
        intensity_factor = 1.0

    # Apply elevation adjustment
    if activity.elevation_gain_meters > 0 and activity.distance_meters > 0:
        elevation_per_km = (activity.elevation_gain_meters / 1000.0) / (activity.distance_meters / 1000.0)
        # Significant elevation (>50m/km) increases load
        if elevation_per_km > 50:
            intensity_factor *= 1.3
        elif elevation_per_km > 30:
            intensity_factor *= 1.15
        elif elevation_per_km > 15:
            intensity_factor *= 1.05

    # Final load score
    load_score = base_load * intensity_factor

    return round(load_score, 2)


def compute_daily_load_scores(
    activities: list[Activity],
    start_date: date,
    end_date: date,
) -> dict[date, float]:
    """Compute daily load scores from activities.

    Args:
        activities: List of activities
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        Dictionary mapping date -> daily load score
    """
    daily_loads: dict[date, float] = {}

    # Initialize all dates in range to 0.0
    current_date = start_date
    while current_date <= end_date:
        daily_loads[current_date] = 0.0
        current_date += timedelta(days=1)

    # Aggregate activities by date
    for activity in activities:
        activity_date = activity.start_time.date()
        if start_date <= activity_date <= end_date:
            load = compute_activity_load(activity)
            daily_loads[activity_date] += load

    return daily_loads


def compute_ctl_atl_tsb_from_loads(
    daily_loads: dict[date, float],
    start_date: date,
    end_date: date,
) -> dict[date, dict[str, float]]:
    """Compute CTL, ATL, TSB from daily load scores.

    Args:
        daily_loads: Dictionary mapping date -> daily load score
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        Dictionary mapping date -> {"ctl": float, "atl": float, "tsb": float}
    """
    # Build continuous series (fill gaps with 0.0)
    continuous_dates: list[date] = []
    daily_load_series: list[float] = []

    current_date = start_date
    while current_date <= end_date:
        continuous_dates.append(current_date)
        daily_load_series.append(daily_loads.get(current_date, 0.0))
        current_date += timedelta(days=1)

    if not daily_load_series:
        return {}

    # Calculate EWMA for CTL (42-day) and ATL (7-day)
    ctl_series = _calculate_ewma(daily_load_series, tau_days=42.0)
    atl_series = _calculate_ewma(daily_load_series, tau_days=7.0)

    # Build result dictionary
    result: dict[date, dict[str, float]] = {}
    for i, date_val in enumerate(continuous_dates):
        ctl = round(ctl_series[i], 2)
        atl = round(atl_series[i], 2)
        tsb = round(ctl - atl, 2)
        result[date_val] = {
            "ctl": ctl,
            "atl": atl,
            "tsb": tsb,
        }

    return result


def _calculate_ewma(values: list[float], tau_days: float) -> list[float]:
    """Calculate exponentially weighted moving average.

    Args:
        values: List of daily values (training load scores)
        tau_days: Time constant in days (42 for CTL, 7 for ATL)

    Returns:
        List of EWMA values, one per input value

    Formula:
        alpha = 1 - exp(-1 / tau)
        ewma[i] = alpha * value[i] + (1 - alpha) * ewma[i-1]
        ewma[0] = value[0] (or 0 if empty)
    """
    if not values:
        return []

    alpha = 1 - math.exp(-1 / tau_days)

    result: list[float] = []
    prev = values[0] if values else 0.0

    for value in values:
        prev = alpha * value + (1 - alpha) * prev
        result.append(round(prev, 2))

    return result
