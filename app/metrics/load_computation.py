"""Load computation engine for training metrics.

Implements the unified daily load metric specification:
- One unified daily load metric (TSS), computed from multiple sensor pathways
- Feeds a single CTL / ATL / Form (FSB) model
- Never track multiple CTLs

Priority order for TSS calculation (highest fidelity first):
1. Power-based TSS (cycling)
2. Pace-based TSS (running / swimming)
3. HR-based TRIMP → mapped to TSS
4. Session-RPE → mapped to TSS

All sports normalized to unified TSS-units for cross-sport comparison.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.db.models import Activity

# Canonical time constants (industry defaults)
TAU_CTL_DAYS = 42.0  # Chronic Training Load time constant
TAU_ATL_DAYS = 7.0  # Acute Training Load time constant

# Default thresholds (will be athlete-specific in future)
DEFAULT_FTP_WATTS = 250.0
DEFAULT_THRESHOLD_PACE_MS = 4.0  # meters per second (~6:57/km or ~11:11/mile)

# TRIMP coefficients (gender-specific)
TRIMP_B_MEN = 1.92
TRIMP_B_WOMEN = 1.67

# Default TRIMP→TSS mapping coefficients (athlete-specific calibration needed)
# Formula: TSS ≈ alpha * TRIMP + beta
DEFAULT_TRIMP_ALPHA = 0.5
DEFAULT_TRIMP_BETA = 0.0

# Default RPE→TSS mapping coefficients (athlete-specific calibration needed)
# Formula: TSS ≈ gamma * sRPE_load + delta
DEFAULT_RPE_GAMMA = 10.0
DEFAULT_RPE_DELTA = 0.0

# Maximum daily TSS spike (safety cap)
MAX_DAILY_TSS = 500.0


class AthleteThresholds:
    """Athlete-specific thresholds for TSS calculation."""

    def __init__(
        self,
        *,
        ftp_watts: float | None = None,
        threshold_pace_ms: float | None = None,
        hr_rest: int | None = None,
        hr_max: int | None = None,
        gender: str | None = None,
        trimp_alpha: float | None = None,
        trimp_beta: float | None = None,
        rpe_gamma: float | None = None,
        rpe_delta: float | None = None,
    ):
        """Initialize athlete thresholds.

        Args:
            ftp_watts: Functional Threshold Power (cycling)
            threshold_pace_ms: Threshold pace (running/swimming) in m/s
            hr_rest: Resting heart rate (bpm)
            hr_max: Maximum heart rate (bpm)
            gender: Gender for TRIMP calculation ("male" or "female")
            trimp_alpha: TRIMP→TSS mapping coefficient (alpha in formula: TSS ≈ alpha * TRIMP + beta)
            trimp_beta: TRIMP→TSS mapping coefficient (beta in formula: TSS ≈ alpha * TRIMP + beta)
            rpe_gamma: RPE→TSS mapping coefficient (gamma in formula: TSS ≈ gamma * sRPE_load + delta)
            rpe_delta: RPE→TSS mapping coefficient (delta in formula: TSS ≈ gamma * sRPE_load + delta)
        """
        self.ftp_watts = ftp_watts or DEFAULT_FTP_WATTS
        self.threshold_pace_ms = threshold_pace_ms or DEFAULT_THRESHOLD_PACE_MS
        self.hr_rest = hr_rest or 60  # Default resting HR
        self.hr_max = hr_max or None
        self.gender = gender or "male"
        self.trimp_alpha = trimp_alpha or DEFAULT_TRIMP_ALPHA
        self.trimp_beta = trimp_beta or DEFAULT_TRIMP_BETA
        self.rpe_gamma = rpe_gamma or DEFAULT_RPE_GAMMA
        self.rpe_delta = rpe_delta or DEFAULT_RPE_DELTA

        # TRIMP coefficient b (gender-specific)
        self.trimp_b = TRIMP_B_MEN if self.gender.lower() in {"male", "m"} else TRIMP_B_WOMEN


def compute_activity_tss(
    activity: Activity,
    athlete_thresholds: AthleteThresholds | None = None,
) -> float:
    """Compute Training Stress Score (TSS) for a single activity.

    Implements unified TSS calculation following specification priority order:
    1. Power-based TSS (cycling)
    2. Pace-based TSS (running/swimming)
    3. HR-based TRIMP → mapped to TSS
    4. Session-RPE → mapped to TSS

    Args:
        activity: Activity record
        athlete_thresholds: Athlete-specific thresholds (uses defaults if None)

    Returns:
        TSS score (float, typically 0-200, can exceed for very long/hard sessions)

    Notes:
        - TSS of 100 = 1 hour at FTP/threshold pace
        - TSS < 50 = recovery/easy session
        - TSS 50-100 = moderate session
        - TSS > 100 = hard/long session
    """
    if activity.duration_seconds is None or activity.duration_seconds <= 0:
        return 0.0

    thresholds = athlete_thresholds or AthleteThresholds()
    raw_data = activity.raw_json or {}
    activity_type = (activity.type or "unknown").lower()

    # Extract data for all pathways
    duration_sec = activity.duration_seconds
    duration_hours = duration_sec / 3600.0
    normalized_power = raw_data.get("weighted_average_watts")
    avg_power = raw_data.get("average_watts")
    avg_hr = raw_data.get("average_heartrate")
    max_hr = raw_data.get("max_heartrate") or thresholds.hr_max
    distance_meters = activity.distance_meters
    streams_data = activity.streams_data or {}
    rpe = raw_data.get("perceived_exertion")  # Session RPE (1-10 scale)

    # Priority 1: Power-based TSS (cycling)
    primary_tss = None
    if activity_type in {"ride", "virtualride", "ebikeride"}:
        primary_tss = _compute_power_based_tss(
            duration_sec=duration_sec,
            normalized_power=normalized_power,
            avg_power=avg_power,
            ftp=thresholds.ftp_watts,
        )
        if primary_tss is not None:
            # Apply multi-sensor adjustment if HR data is available
            if avg_hr and max_hr:
                # Estimate expected HR from power intensity
                np = normalized_power or avg_power
                if np is not None:
                    intensity_factor = np / thresholds.ftp_watts
                    expected_hr = thresholds.hr_rest + (max_hr - thresholds.hr_rest) * intensity_factor
                    primary_tss = _apply_multi_sensor_adjustment(
                        primary_tss=primary_tss,
                        expected_hr=expected_hr,
                        actual_hr=avg_hr,
                        hr_max=max_hr,
                    )
            return _apply_guardrails(primary_tss, activity, duration_sec)

    # Priority 2: Pace-based TSS (running/swimming)
    if activity_type in {"run", "trail run", "walk", "swim"}:
        primary_tss = _compute_pace_based_tss(
            duration_hours=duration_hours,
            distance_meters=distance_meters,
            activity_type=activity_type,
            threshold_pace_ms=thresholds.threshold_pace_ms,
            streams_data=streams_data,
            elevation_gain_meters=activity.elevation_gain_meters,
        )
        if primary_tss is not None:
            # Apply multi-sensor adjustment if HR data is available
            if avg_hr and max_hr:
                # Estimate expected HR from pace intensity
                v_norm = _calculate_normalized_pace(
                    duration_hours=duration_hours,
                    distance_meters=distance_meters,
                    streams_data=streams_data,
                    elevation_gain_meters=activity.elevation_gain_meters,
                    activity_type=activity_type,
                )
                if v_norm and v_norm > 0:
                    intensity_factor = v_norm / thresholds.threshold_pace_ms
                    expected_hr = thresholds.hr_rest + (max_hr - thresholds.hr_rest) * intensity_factor
                    primary_tss = _apply_multi_sensor_adjustment(
                        primary_tss=primary_tss,
                        expected_hr=expected_hr,
                        actual_hr=avg_hr,
                        hr_max=max_hr,
                    )
            return _apply_guardrails(primary_tss, activity, duration_sec)

    # Priority 3: HR-based TRIMP → mapped to TSS
    if avg_hr and max_hr and max_hr > thresholds.hr_rest:
        trimp = _compute_trimp(
            duration_min=duration_sec / 60.0,
            avg_hr=avg_hr,
            hr_rest=thresholds.hr_rest,
            hr_max=max_hr,
            trimp_b=thresholds.trimp_b,
        )
        tss = _map_trimp_to_tss(trimp, thresholds.trimp_alpha, thresholds.trimp_beta)
        return _apply_guardrails(tss, activity, duration_sec)

    # Priority 4: Session-RPE → mapped to TSS
    if rpe is not None and 1 <= rpe <= 10:
        srpe_load = rpe * (duration_sec / 60.0)  # RPE * Duration_min
        tss = _map_rpe_to_tss(srpe_load, thresholds.rpe_gamma, thresholds.rpe_delta)
        return _apply_guardrails(tss, activity, duration_sec)

    # Fallback: Return 0 for activities without sufficient data
    return 0.0


def _compute_power_based_tss(
    duration_sec: float,
    normalized_power: float | None,
    avg_power: float | None,
    ftp: float,
) -> float | None:
    """Compute power-based TSS (cycling).

    Formula: TSS = (t_sec * NP * IF) / (FTP * 3600) * 100
    Where: IF = NP / FTP

    Args:
        duration_sec: Duration in seconds
        normalized_power: Normalized Power (watts) - preferred
        avg_power: Average power (watts) - fallback if NP unavailable
        ftp: Functional Threshold Power (watts)

    Returns:
        TSS score or None if insufficient data
    """
    if ftp <= 0:
        return None

    # Prefer normalized power, fallback to average power
    np = normalized_power or avg_power
    if np is None or np <= 0:
        return None

    # Calculate Intensity Factor
    intensity_factor = np / ftp

    # Power-based TSS formula from specification
    tss = (duration_sec * np * intensity_factor) / (ftp * 3600.0) * 100.0

    return round(tss, 1)


def _compute_pace_based_tss(
    *,
    duration_hours: float,
    distance_meters: float | None,
    activity_type: str,
    threshold_pace_ms: float,
    streams_data: dict,
    elevation_gain_meters: float | None,
) -> float | None:
    """Compute pace-based TSS (running/swimming).

    Formula: IF_run = v_norm / v_thr
    Formula: rTSS = t_hr * IF_run^2 * 100

    Args:
        duration_hours: Duration in hours
        distance_meters: Distance in meters
        activity_type: Activity type
        threshold_pace_ms: Threshold pace in m/s
        streams_data: Streams data for normalized/grade-adjusted pace
        elevation_gain_meters: Elevation gain in meters

    Returns:
        TSS score or None if insufficient data
    """
    if threshold_pace_ms <= 0 or duration_hours <= 0:
        return None

    # Calculate normalized/grade-adjusted pace
    v_norm = _calculate_normalized_pace(
        duration_hours=duration_hours,
        distance_meters=distance_meters,
        streams_data=streams_data,
        elevation_gain_meters=elevation_gain_meters,
        activity_type=activity_type,
    )

    if v_norm is None or v_norm <= 0:
        return None

    # Calculate Intensity Factor
    intensity_factor = v_norm / threshold_pace_ms

    # Pace-based TSS formula from specification
    tss = duration_hours * (intensity_factor**2) * 100.0

    return round(tss, 1)


def _calculate_normalized_pace(
    duration_hours: float,
    distance_meters: float | None,
    streams_data: dict,
    elevation_gain_meters: float | None,
    activity_type: str,
) -> float | None:
    """Calculate normalized or grade-adjusted pace.

    Prefers grade-adjusted pace from streams if available.
    Falls back to average pace, adjusted for elevation.

    Args:
        duration_hours: Duration in hours
        distance_meters: Distance in meters
        streams_data: Streams data (may contain pace/velocity_smooth)
        elevation_gain_meters: Elevation gain in meters
        activity_type: Activity type - reserved for future activity-specific adjustments

    Returns:
        Normalized pace in m/s or None
    """
    # activity_type is reserved for future activity-specific pace adjustments
    _ = activity_type
    # Try to get grade-adjusted or normalized pace from streams
    if streams_data:
        # Look for velocity_smooth (m/s) or pace data
        velocity_data = streams_data.get("velocity_smooth")
        if velocity_data and isinstance(velocity_data, list) and len(velocity_data) > 0:
            # Use average velocity from streams
            valid_velocities = [v for v in velocity_data if isinstance(v, (int, float)) and v > 0]
            if valid_velocities:
                avg_velocity = sum(valid_velocities) / len(valid_velocities)
                return float(avg_velocity)

    # Fallback: Calculate from distance and duration
    if distance_meters and distance_meters > 0 and duration_hours > 0:
        # Pace in m/s = distance (m) / duration (s)
        avg_pace_ms = distance_meters / (duration_hours * 3600.0)

        # Apply elevation adjustment if available
        if elevation_gain_meters and elevation_gain_meters > 0 and distance_meters > 0:
            elevation_per_km = (elevation_gain_meters / 1000.0) / (distance_meters / 1000.0)
            # Grade adjustment factor (approximate)
            if elevation_per_km > 50:
                avg_pace_ms *= 0.85  # Very hilly - pace slower but effort higher
            elif elevation_per_km > 30:
                avg_pace_ms *= 0.90
            elif elevation_per_km > 15:
                avg_pace_ms *= 0.95

        return avg_pace_ms

    return None


def _compute_trimp(
    duration_min: float,
    avg_hr: int,
    hr_rest: int,
    hr_max: int,
    trimp_b: float,
) -> float:
    """Compute HR-based TRIMP.

    Formula: ΔHR = (HR_avg - HR_rest) / (HR_max - HR_rest)
    Formula: TRIMP = D_min * ΔHR * e^(b * ΔHR)

    Args:
        duration_min: Duration in minutes
        avg_hr: Average heart rate (bpm)
        hr_rest: Resting heart rate (bpm)
        hr_max: Maximum heart rate (bpm)
        trimp_b: Gender-specific coefficient (1.92 for men, 1.67 for women)

    Returns:
        TRIMP score
    """
    if hr_max <= hr_rest:
        return 0.0

    delta_hr = (avg_hr - hr_rest) / (hr_max - hr_rest)
    delta_hr = max(0.0, min(1.0, delta_hr))  # Clamp to [0, 1]

    trimp = duration_min * delta_hr * math.exp(trimp_b * delta_hr)

    return round(trimp, 1)


def _map_trimp_to_tss(trimp: float, alpha: float, beta: float) -> float:
    """Map TRIMP to TSS using athlete-specific regression.

    Formula: TSS ≈ alpha * TRIMP + beta
    or: TSS ≈ alpha * TRIMP (if beta=0)

    Args:
        trimp: TRIMP score
        alpha: TRIMP→TSS mapping coefficient
        beta: TRIMP→TSS mapping coefficient (intercept)

    Returns:
        TSS score
    """
    tss = alpha * trimp + beta
    return round(max(0.0, tss), 1)


def _map_rpe_to_tss(srpe_load: float, gamma: float, delta: float) -> float:
    """Map session-RPE load to TSS.

    Formula: srpe_load = RPE * Duration_min
    Formula: TSS ≈ gamma * srpe_load + delta

    Args:
        srpe_load: Session RPE load (RPE * Duration_min)
        gamma: RPE→TSS mapping coefficient
        delta: RPE→TSS mapping coefficient (intercept)

    Returns:
        TSS score
    """
    tss = gamma * srpe_load + delta
    return round(max(0.0, tss), 1)


def _apply_multi_sensor_adjustment(
    primary_tss: float,
    expected_hr: float | None,
    actual_hr: int | None,
    hr_max: int | None,
) -> float:
    """Apply multi-sensor adjustment (optional, bounded).

    If power/pace + HR exist:
        Adj = clamp(1 + k * (HR_actual - HR_expected), 0.9, 1.1)
        TSS_final = TSS_primary * Adj

    Purpose: Account for heat, fatigue, dehydration, altitude

    Args:
        primary_tss: Primary TSS from power/pace
        expected_hr: Expected HR for given intensity
        actual_hr: Actual average HR
        hr_max: Maximum HR

    Returns:
        Adjusted TSS
    """
    if expected_hr is None or actual_hr is None or hr_max is None:
        return primary_tss

    if hr_max <= 0:
        return primary_tss

    # Simple k factor (can be refined)
    k = 0.002  # Adjustment factor

    hr_diff = actual_hr - expected_hr
    adjustment = 1.0 + (k * hr_diff)
    adjustment = max(0.9, min(1.1, adjustment))  # Clamp to [0.9, 1.1]

    adjusted_tss = primary_tss * adjustment
    return round(adjusted_tss, 1)


def _apply_guardrails(tss: float, _activity: Activity, duration_sec: float) -> float:
    """Apply guardrails and data hygiene.

    - Hard caps on daily TSS spikes
    - Sensor confidence scoring (basic implementation)

    Args:
        tss: Computed TSS
        activity: Activity record
        duration_sec: Duration in seconds

    Returns:
        TSS with guardrails applied
    """
    # Cap excessive TSS spikes
    tss = min(tss, MAX_DAILY_TSS)

    # Basic validation: very short duration with high TSS is suspicious
    if duration_sec < 60 and tss > 100:
        tss = min(tss, 50.0)  # Cap for very short activities

    return max(0.0, tss)


def compute_daily_tss_load(
    activities: list[Activity],
    start_date: date,
    end_date: date,
    athlete_thresholds: AthleteThresholds | None = None,
) -> dict[date, float]:
    """Compute daily TSS load from activities.

    For each day: Sum all session TSS
    If rest day: Load_t = 0

    Args:
        activities: List of activities
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        athlete_thresholds: Athlete-specific thresholds

    Returns:
        Dictionary mapping date -> daily TSS load
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
            tss = compute_activity_tss(activity, athlete_thresholds)
            daily_loads[activity_date] += tss

    return daily_loads


def compute_ctl_atl_form_from_tss(
    daily_tss_loads: dict[date, float],
    start_date: date,
    end_date: date,
) -> dict[date, dict[str, float]]:
    """Compute CTL, ATL, and Form (FSB) from daily TSS loads.

    Canonical formulas:
    - CTL[t] = CTL[t-1] + (Load[t] - CTL[t-1]) / 42
    - ATL[t] = ATL[t-1] + (Load[t] - ATL[t-1]) / 7
    - Form[t] = CTL[t-1] - ATL[t-1]  (Yesterday's values avoid same-day artifacts)

    Args:
        daily_tss_loads: Dictionary mapping date -> daily TSS load
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        Dictionary mapping date -> {"ctl": float, "atl": float, "fsb": float}
        Note: Form is computed from previous day's CTL/ATL

    Notes:
        - Missing days are treated as rest days (Load_t = 0.0)
        - All dates in range are included (continuous series)
        - Deterministic and idempotent
    """
    # Build continuous series (fill gaps with 0.0 for rest days)
    continuous_dates: list[date] = []
    daily_load_series: list[float] = []

    current_date = start_date
    while current_date <= end_date:
        continuous_dates.append(current_date)
        daily_load_series.append(daily_tss_loads.get(current_date, 0.0))
        current_date += timedelta(days=1)

    if not daily_load_series:
        return {}

    # Calculate EWMA for CTL (42-day) and ATL (7-day)
    ctl_series = _calculate_ewma(daily_load_series, tau_days=TAU_CTL_DAYS)
    atl_series = _calculate_ewma(daily_load_series, tau_days=TAU_ATL_DAYS)

    # Build result dictionary
    result: dict[date, dict[str, float]] = {}
    for i, date_val in enumerate(continuous_dates):
        ctl = round(ctl_series[i], 2)
        atl = round(atl_series[i], 2)

        # Form (FSB) = CTL[t-1] - ATL[t-1] (yesterday's values)
        if i > 0:
            form = round(ctl_series[i - 1] - atl_series[i - 1], 2)
        else:
            form = round(ctl - atl, 2)  # First day uses same-day values

        result[date_val] = {
            "ctl": ctl,
            "atl": atl,
            "fsb": form,  # Form/Freshness (TSB)
        }

    return result


def _calculate_ewma(values: list[float], tau_days: float) -> list[float]:
    """Calculate exponentially weighted moving average (EWMA).

    Implements the canonical EMA formula used in training load models.

    Formula from specification:
        CTL[t] = CTL[t-1] + (Load[t] - CTL[t-1]) / τ

    This is equivalent to:
        alpha = 1 / tau
        EWMA[t] = alpha * value[t] + (1 - alpha) * EWMA[t-1]

    Where:
        - tau: Time constant (days to reach ~63% of new value)
        - For CTL: tau = 42 days
        - For ATL: tau = 7 days

    Args:
        values: List of daily values (TSS loads)
        tau_days: Time constant in days (42 for CTL, 7 for ATL)

    Returns:
        List of EWMA values, one per input value

    Notes:
        - First value initializes the EWMA
        - Missing days (0.0) are treated as rest days, not gaps
        - Uses specification formula: EWMA[t] = EWMA[t-1] + (value[t] - EWMA[t-1]) / tau
    """
    if not values:
        return []

    result: list[float] = []
    prev = values[0] if values else 0.0

    for value in values:
        # Specification formula: EWMA[t] = EWMA[t-1] + (value[t] - EWMA[t-1]) / tau
        prev += (value - prev) / tau_days
        result.append(round(prev, 2))

    return result


# Legacy compatibility functions (for backward compatibility)
def compute_activity_load(activity: Activity) -> float:
    """Legacy function: Compute Daily Training Load (DTL).

    Deprecated: Use compute_activity_tss() instead.
    This function is kept for backward compatibility.

    Args:
        activity: Activity record

    Returns:
        DTL score (calls compute_activity_tss internally)
    """
    return compute_activity_tss(activity)


def compute_daily_load_scores(
    activities: list[Activity],
    start_date: date,
    end_date: date,
) -> dict[date, float]:
    """Legacy function: Compute daily load scores.

    Deprecated: Use compute_daily_tss_load() instead.
    This function is kept for backward compatibility.

    Args:
        activities: List of activities
        start_date: Start date (inclusive)
        end_date: End date (inclusive)

    Returns:
        Dictionary mapping date -> daily load score (TSS)
    """
    return compute_daily_tss_load(activities, start_date, end_date)


def compute_ctl_atl_tsb_from_loads(
    daily_loads: dict[date, float],
    start_date: date,
    end_date: date,
    _normalize: bool = True,
) -> dict[date, dict[str, float]]:
    """Legacy function: Compute CTL, ATL, TSB from daily loads.

    Deprecated: Use compute_ctl_atl_form_from_tss() instead.
    This function is kept for backward compatibility.

    Args:
        daily_loads: Dictionary mapping date -> daily TSS load
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
        normalize: Ignored (kept for compatibility)

    Returns:
        Dictionary mapping date -> {"ctl": float, "atl": float, "tsb": float}
        Note: "tsb" is actually Form (FSB) value
    """
    result = compute_ctl_atl_form_from_tss(daily_loads, start_date, end_date)
    # Map "fsb" to "tsb" for backward compatibility
    return {date_val: {**vals, "tsb": vals.get("fsb", 0.0)} for date_val, vals in result.items()}


def _normalize_to_scale(value: float, max_value: float = 100.0) -> float:
    """Normalize a metric value to -100 to 100 scale.

    Note: This normalization is NOT used in the specification.
    Kept for backward compatibility only.

    Args:
        value: Metric value (typically 0-max_value)
        max_value: Maximum expected value for normalization (default 100)

    Returns:
        Normalized value in -100 to 100 range
    """
    normalized = (value / max_value) * 200.0 - 100.0
    return round(max(-100.0, min(100.0, normalized)), 2)
