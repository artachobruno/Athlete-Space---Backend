"""Load computation engine for training metrics.

Implements canonical training load model:
- DTL (Daily Training Load) = f(intensity, duration, modality)
- CTL (Chronic Training Load) = 42-day EWMA of DTL
- ATL (Acute Training Load) = 7-day EWMA of DTL
- TSB (Training Stress Balance) = CTL - ATL

All sports normalized to unified internal unit for cross-sport comparison.

Phase 1: Basic EMA-based CTL/ATL with unified DTL
Phase 2: Athlete-specific τ, confidence intervals, recovery modifiers (future)
Phase 3: Predictive TSB, risk scoring, AI explanations (future)
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.db.models import Activity

# Canonical time constants (industry defaults)
TAU_CTL_DAYS = 42.0  # Chronic Training Load time constant
TAU_ATL_DAYS = 7.0  # Acute Training Load time constant

# Modality factors (normalize different sports to unified unit)
# Run > Bike > Swim (relative strain impact)
MODALITY_FACTORS: dict[str, float] = {
    "run": 1.0,  # Baseline (highest impact)
    "trail run": 1.1,  # Slightly higher due to terrain
    "walk": 0.3,  # Low impact
    "ride": 0.7,  # Lower impact than running
    "virtualride": 0.7,
    "ebikeride": 0.4,  # E-bike is lower intensity
    "swim": 0.5,  # Lower impact, different muscle groups
    "default": 0.8,  # Default for unknown activities
}


def compute_activity_load(activity: Activity) -> float:
    """Compute Daily Training Load (DTL) for a single activity.

    Formula: DTL = duration x intensity_factor x modality_factor x personalization_factor

    Currently implements:
    - duration: Activity duration in hours
    - intensity_factor: Based on HR, power, pace vs baseline
    - modality_factor: Sport-specific normalization (run > bike > swim)
    - personalization_factor: Defaults to 1.0 (Phase 2: athlete-specific)

    Args:
        activity: Activity record

    Returns:
        DTL score (float, unified unit across all sports)

    Notes:
        - All sports normalized to same internal unit
        - HR-based intensity preferred when available
        - Power-based for cycling when available
        - Pace-based fallback for running
        - Duration-based fallback for other activities
    """
    if activity.duration_seconds is None:
        return 0.0
    duration_hours = activity.duration_seconds / 3600.0

    # Extract raw data for intensity estimation
    raw_data = activity.raw_json or {}
    avg_hr = raw_data.get("average_heartrate")
    avg_power = raw_data.get("average_watts")
    max_hr = raw_data.get("max_heartrate")

    activity_type = (activity.type or "unknown").lower()

    # Step 1: Calculate intensity_factor
    intensity_factor = _compute_intensity_factor(
        activity_type=activity_type,
        duration_hours=duration_hours,
        distance_meters=activity.distance_meters or 0.0,
        avg_hr=avg_hr,
        max_hr=max_hr,
        avg_power=avg_power,
        elevation_gain_meters=activity.elevation_gain_meters or 0.0,
    )

    # Step 2: Get modality_factor (sport normalization)
    modality_factor = MODALITY_FACTORS.get(activity_type, MODALITY_FACTORS["default"])

    # Step 3: Personalization factor (Phase 2: athlete-specific)
    # For now, default to 1.0 (no personalization)
    personalization_factor = 1.0

    # Final DTL calculation
    dtl = duration_hours * intensity_factor * modality_factor * personalization_factor

    return round(dtl, 2)


def compute_activity_tss(activity: Activity) -> float:
    """Compute Training Stress Score (TSS) for a single activity.

    TSS is a standardized metric (0-100+ scale) that quantifies training stress.
    Formula: TSS = (duration x IF²) x 100
    Where IF = Intensity Factor (0.0-1.0+)

    For cycling: IF = normalized_power / FTP (if available)
    For running: IF = HR-based or pace-based intensity
    For other sports: IF = estimated from available metrics

    Args:
        activity: Activity record

    Returns:
        TSS score (float, typically 0-200, can exceed for very long/hard sessions)

    Notes:
        - TSS of 100 = 1 hour at FTP/threshold pace
        - TSS < 50 = recovery/easy session
        - TSS 50-100 = moderate session
        - TSS > 100 = hard/long session
    """
    if activity.duration_seconds is None:
        return 0.0
    duration_hours = activity.duration_seconds / 3600.0

    # Extract raw data for intensity estimation
    raw_data = activity.raw_json or {}
    avg_hr = raw_data.get("average_heartrate")
    avg_power = raw_data.get("average_watts")
    max_hr = raw_data.get("max_heartrate")
    normalized_power = raw_data.get("weighted_average_watts")  # NP if available

    activity_type = (activity.type or "unknown").lower()

    # Calculate Intensity Factor (IF)
    # IF ranges from 0.0 (rest) to 1.0+ (above threshold)
    if activity_type in {"ride", "virtualride"} and normalized_power:
        # For cycling with normalized power, estimate IF
        # Assuming average FTP of 250W (will be personalized in Phase 2)
        estimated_ftp = 250.0
        intensity_factor = min(normalized_power / estimated_ftp, 1.5)  # Cap at 1.5x FTP
    elif activity_type in {"ride", "virtualride"} and avg_power:
        # Fallback: use average power with estimated FTP
        estimated_ftp = 250.0
        intensity_factor = min(avg_power / estimated_ftp, 1.5)
    elif avg_hr and max_hr and max_hr > 0:
        # HR-based IF (works for all sports)
        hr_ratio = avg_hr / max_hr
        # Map HR zones to IF: 50-60% = 0.5, 60-70% = 0.65, 70-80% = 0.8, 80-90% = 0.95, 90%+ = 1.1
        if hr_ratio < 0.6:
            intensity_factor = 0.5
        elif hr_ratio < 0.7:
            intensity_factor = 0.65
        elif hr_ratio < 0.8:
            intensity_factor = 0.8
        elif hr_ratio < 0.9:
            intensity_factor = 0.95
        else:
            intensity_factor = 1.1
    else:
        # Fallback: use the same intensity factor calculation as DTL
        intensity_factor = _compute_intensity_factor(
            activity_type=activity_type,
            duration_hours=duration_hours,
            distance_meters=activity.distance_meters or 0.0,
            avg_hr=avg_hr,
            max_hr=max_hr,
            avg_power=avg_power,
            elevation_gain_meters=activity.elevation_gain_meters or 0.0,
        )
        # Normalize to 0.0-1.0+ range for IF
        intensity_factor = min(intensity_factor / 1.5, 1.2)  # Normalize our intensity factor to IF range

    # TSS = (duration in hours x IF²) x 100
    tss = (duration_hours * intensity_factor * intensity_factor) * 100.0

    return round(tss, 1)


def _compute_intensity_factor(
    *,
    activity_type: str,
    duration_hours: float,
    distance_meters: float | None,
    avg_hr: int | None,
    max_hr: int | None,
    avg_power: float | None,
    elevation_gain_meters: float | None,
) -> float:
    """Compute intensity factor from available metrics.

    Priority:
    1. HR-based (most accurate for all sports)
    2. Power-based (cycling)
    3. Pace-based (running)
    4. Duration-based (fallback)

    Args:
        activity_type: Activity type (run, ride, swim, etc.)
        duration_hours: Duration in hours
        distance_meters: Distance in meters
        avg_hr: Average heart rate (bpm)
        max_hr: Maximum heart rate (bpm)
        avg_power: Average power (watts)
        elevation_gain_meters: Elevation gain in meters

    Returns:
        Intensity factor (typically 0.5-2.5)
    """
    # HR-based intensity (preferred when available)
    if avg_hr and max_hr and max_hr > 0:
        return _intensity_from_hr(avg_hr, max_hr)

    # Power-based intensity (cycling)
    if activity_type in {"ride", "virtualride"} and avg_power:
        return _intensity_from_power(avg_power)

    # Pace-based intensity (running)
    if activity_type in {"run", "trail run"} and distance_meters is not None and distance_meters > 0 and duration_hours > 0:
        elevation = elevation_gain_meters or 0.0
        return _intensity_from_pace(distance_meters, duration_hours, elevation)

    # Duration-based fallback
    return _intensity_from_duration(duration_hours)


def _intensity_from_hr(avg_hr: int, max_hr: int) -> float:
    """Compute intensity from heart rate zones.

    Args:
        avg_hr: Average heart rate (bpm)
        max_hr: Maximum heart rate (bpm)

    Returns:
        Intensity factor (0.6-2.0)
    """
    hr_ratio = avg_hr / max_hr
    if hr_ratio < 0.6:
        return 0.6
    if hr_ratio < 0.7:
        return 0.8
    if hr_ratio < 0.8:
        return 1.0
    if hr_ratio < 0.9:
        return 1.5
    return 2.0


def _intensity_from_power(avg_power: float) -> float:
    """Compute intensity from power (cycling).

    Args:
        avg_power: Average power (watts)

    Returns:
        Intensity factor (0.5-2.5)
    """
    normalized_power = avg_power / 250.0
    return max(0.5, min(2.5, normalized_power))


def _intensity_from_pace(distance_meters: float, duration_hours: float, elevation_gain_meters: float) -> float:
    """Compute intensity from pace (running).

    Args:
        distance_meters: Distance in meters
        duration_hours: Duration in hours
        elevation_gain_meters: Elevation gain in meters

    Returns:
        Intensity factor (0.6-2.0+)
    """
    pace_kmh = (distance_meters / 1000.0) / duration_hours
    if pace_kmh < 8:
        intensity = 0.6
    elif pace_kmh < 10:
        intensity = 0.8
    elif pace_kmh < 12:
        intensity = 1.0
    elif pace_kmh < 15:
        intensity = 1.5
    else:
        intensity = 2.0

    # Elevation adjustment
    if elevation_gain_meters > 0:
        elevation_per_km = (elevation_gain_meters / 1000.0) / (distance_meters / 1000.0)
        if elevation_per_km > 50:
            intensity *= 1.3
        elif elevation_per_km > 30:
            intensity *= 1.15
        elif elevation_per_km > 15:
            intensity *= 1.05

    return intensity


def _intensity_from_duration(duration_hours: float) -> float:
    """Compute intensity from duration (fallback).

    Args:
        duration_hours: Duration in hours

    Returns:
        Intensity factor (0.7-1.5)
    """
    if duration_hours > 4:
        return 0.7
    if duration_hours > 2:
        return 1.0
    if duration_hours > 1:
        return 1.2
    return 1.5


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


def _normalize_to_scale(value: float, max_value: float = 100.0) -> float:
    """Normalize a metric value to -100 to 100 scale.

    Args:
        value: Metric value (typically 0-max_value)
        max_value: Maximum expected value for normalization (default 100)

    Returns:
        Normalized value in -100 to 100 range
    """
    normalized = (value / max_value) * 200.0 - 100.0
    return round(max(-100.0, min(100.0, normalized)), 2)


def compute_ctl_atl_tsb_from_loads(
    daily_loads: dict[date, float],
    start_date: date,
    end_date: date,
    normalize: bool = True,
) -> dict[date, dict[str, float]]:
    """Compute CTL, ATL, TSB from daily training loads (DTL).

    Canonical formulas:
    - CTL[t] = CTL[t-1] + (DTL[t] - CTL[t-1]) / τ_CTL
    - ATL[t] = ATL[t-1] + (DTL[t] - ATL[t-1]) / τ_ATL
    - TSB[t] = CTL[t] - ATL[t]

    Where:
    - τ_CTL = 42 days (chronic fitness, what you can sustain)
    - τ_ATL = 7 days (acute fatigue, what you're currently absorbing)

    Args:
        daily_loads: Dictionary mapping date -> DTL (Daily Training Load)
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        normalize: If True, normalize CTL and ATL to -100 to 100 scale (default: True)

    Returns:
        Dictionary mapping date -> {"ctl": float, "atl": float, "tsb": float}
        Values are normalized to -100 to 100 scale if normalize=True

    Notes:
        - Missing days are treated as rest days (DTL = 0.0)
        - All dates in range are included (continuous series)
        - Deterministic and idempotent
        - CTL and ATL are normalized to -100 to 100 scale by default
    """
    # Build continuous series (fill gaps with 0.0 for rest days)
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
    ctl_series = _calculate_ewma(daily_load_series, tau_days=TAU_CTL_DAYS)
    atl_series = _calculate_ewma(daily_load_series, tau_days=TAU_ATL_DAYS)

    # Build result dictionary
    result: dict[date, dict[str, float]] = {}
    for i, date_val in enumerate(continuous_dates):
        ctl = ctl_series[i]
        atl = atl_series[i]

        if normalize:
            # Normalize CTL and ATL to -100 to 100 scale
            ctl = _normalize_to_scale(ctl)
            atl = _normalize_to_scale(atl)
        else:
            ctl = round(ctl, 2)
            atl = round(atl, 2)

        tsb = round(ctl - atl, 2)
        result[date_val] = {
            "ctl": ctl,
            "atl": atl,
            "tsb": tsb,
        }

    return result


def _calculate_ewma(values: list[float], tau_days: float) -> list[float]:
    """Calculate exponentially weighted moving average (EWMA).

    Implements the canonical EMA formula used in training load models.

    Formula:
        alpha = 1 - exp(-1 / tau)
        EWMA[t] = alpha * value[t] + (1 - alpha) * EWMA[t-1]

    Where:
        - tau: Time constant (days to reach ~63% of new value)
        - alpha: Smoothing factor (higher = more responsive)

    Args:
        values: List of daily values (DTL scores)
        tau_days: Time constant in days (42 for CTL, 7 for ATL)

    Returns:
        List of EWMA values, one per input value

    Notes:
        - First value initializes the EWMA
        - Missing days (0.0) are treated as rest days, not gaps
        - Same formula as TrainingPeaks/WKO5 industry standard
    """
    if not values:
        return []

    # Calculate smoothing factor
    # alpha = 1 - e^(-1/tau) where tau is the time constant
    alpha = 1 - math.exp(-1 / tau_days)

    result: list[float] = []
    prev = values[0] if values else 0.0

    for value in values:
        # EWMA: weighted average of current value and previous EWMA
        prev = alpha * value + (1 - alpha) * prev
        result.append(round(prev, 2))

    return result
