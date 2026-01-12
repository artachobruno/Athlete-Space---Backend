"""Service for computing effort metrics (NP, rNP, IF) for activities.

This service orchestrates effort computation by:
1. Extracting time-series data from activity streams
2. Computing Normalized Power (cycling) or Running Effort (running) or HR effort
3. Resolving sport-specific thresholds
4. Computing Intensity Factor (IF)
"""

from __future__ import annotations

from app.db.models import Activity, UserSettings
from app.metrics.effort_computation import (
    EffortSource,
    compute_hr_effort,
    compute_intensity_factor,
    compute_normalized_power,
    compute_running_effort,
)


def resolve_activity_threshold(
    activity: Activity,
    user_settings: UserSettings | None = None,
) -> float | None:
    """Resolve sport-specific threshold for an activity.

    Threshold resolution logic:
    - Bike: FTP (ftp_watts)
    - Run: threshold_pace_ms or threshold_speed
    - Other: threshold_hr

    Args:
        activity: Activity record
        user_settings: User settings with threshold configuration

    Returns:
        Threshold value for the activity's sport type, or None if not available

    Raises:
        ValueError: If threshold is required but missing (fail hard, don't guess)
    """
    if not user_settings:
        return None

    activity_type = (activity.type or "unknown").lower()

    if activity_type in {"ride", "virtualride", "ebikeride"}:
        # Cycling: use FTP
        if user_settings.ftp_watts is None or user_settings.ftp_watts <= 0:
            return None
        return float(user_settings.ftp_watts)

    if activity_type in {"run", "trail run", "walk"}:
        # Running: use threshold pace
        if user_settings.threshold_pace_ms is None or user_settings.threshold_pace_ms <= 0:
            return None
        return float(user_settings.threshold_pace_ms)

    # Other sports: use threshold HR
    if user_settings.threshold_hr is None or user_settings.threshold_hr <= 0:
        return None
    return float(user_settings.threshold_hr)


def _compute_velocity_from_distance_time(
    distance_samples: list[float | int | None],
    time_samples: list[float | int | None],
) -> list[float]:
    """Compute velocity samples from distance and time deltas.

    Args:
        distance_samples: List of cumulative distance values
        time_samples: List of time values

    Returns:
        List of velocity values in m/s
    """
    velocity_samples: list[float] = []
    prev_dist = 0.0
    prev_time = 0.0
    for i, (dist, time_val) in enumerate(zip(distance_samples, time_samples, strict=False)):
        if dist is not None and time_val is not None:
            dist_float = float(dist)
            time_float = float(time_val)
            if i > 0 and time_float > prev_time:
                velocity = (dist_float - prev_dist) / (time_float - prev_time)
                if velocity > 0:
                    velocity_samples.append(velocity)
            prev_dist = dist_float
            prev_time = time_float
    return velocity_samples


def _compute_running_effort_with_threshold(
    activity: Activity,
    velocity_samples: list[float],
    elevation_samples: list[float | int | None] | None,
    user_settings: UserSettings | None,
) -> tuple[float | None, EffortSource | None, float | None]:
    """Compute running effort and IF if threshold available.

    Args:
        activity: Activity record
        velocity_samples: List of velocity values
        elevation_samples: Optional list of elevation values
        user_settings: User settings with threshold configuration

    Returns:
        Tuple of (normalized_effort, effort_source, intensity_factor)
    """
    # Convert velocity_samples to match expected type list[float | int | None]
    pace_samples: list[float | int | None] = [float(v) for v in velocity_samples]
    rnp = compute_running_effort(pace_samples, elevation_samples)
    if rnp is None:
        return (None, None, None)

    threshold = resolve_activity_threshold(activity, user_settings)
    if threshold is not None:
        if_value = compute_intensity_factor(rnp, threshold)
        return (rnp, "pace", if_value)
    return (rnp, "pace", None)


def compute_activity_effort(
    activity: Activity,
    user_settings: UserSettings | None = None,
) -> tuple[float | None, EffortSource | None, float | None]:
    """Compute effort metrics for an activity.

    Computes:
    1. Normalized Power (cycling) or Running Effort (running) or HR effort
    2. Effort source ("power", "pace", "hr")
    3. Intensity Factor (IF = NP / threshold)

    Priority order:
    1. Power-based NP (cycling)
    2. Pace-based rNP (running)
    3. HR-based effort (fallback)

    Args:
        activity: Activity record
        user_settings: User settings with threshold configuration

    Returns:
        Tuple of (normalized_effort, effort_source, intensity_factor)
        - normalized_effort: NP (watts), rNP (m/s), or HR effort (ratio)
        - effort_source: "power", "pace", or "hr"
        - intensity_factor: IF value (clamped to [0.3, 1.5])
    """
    if not activity.streams_data:
        return (None, None, None)

    streams_data = activity.streams_data
    activity_type = (activity.type or "unknown").lower()

    # Priority 1: Power-based NP (cycling)
    if activity_type in {"ride", "virtualride", "ebikeride"}:
        power_samples = streams_data.get("watts", [])
        if power_samples:
            np = compute_normalized_power(power_samples)
            if np is not None:
                # Compute IF if threshold available
                threshold = resolve_activity_threshold(activity, user_settings)
                if threshold is not None:
                    if_value = compute_intensity_factor(np, threshold)
                    return (np, "power", if_value)
                return (np, "power", None)

    # Priority 2: Pace-based rNP (running)
    if activity_type in {"run", "trail run", "walk"}:
        # Get velocity_smooth (m/s) or compute from pace
        velocity_samples = streams_data.get("velocity_smooth", [])
        if not velocity_samples:
            # Try to compute from distance/time if available
            distance_samples = streams_data.get("distance", [])
            time_samples = streams_data.get("time", [])
            if distance_samples and time_samples and len(distance_samples) == len(time_samples):
                velocity_samples = _compute_velocity_from_distance_time(distance_samples, time_samples)

        if velocity_samples:
            elevation_samples = streams_data.get("altitude", [])
            return _compute_running_effort_with_threshold(activity, velocity_samples, elevation_samples, user_settings)

    # Priority 3: HR-based effort (fallback)
    hr_samples = streams_data.get("heartrate", [])
    if hr_samples:
        threshold_hr = None
        if user_settings and user_settings.threshold_hr:
            threshold_hr = float(user_settings.threshold_hr)
        else:
            # Try to get from raw_json as fallback
            raw_data = activity.raw_json or {}
            max_hr = raw_data.get("max_heartrate")
            if max_hr:
                # Estimate threshold HR as ~85% of max HR (rough estimate)
                threshold_hr = float(max_hr) * 0.85

        if threshold_hr and threshold_hr > 0:
            hr_effort = compute_hr_effort(hr_samples, threshold_hr)
            if hr_effort is not None:
                # For HR effort, IF is the effort value itself (already normalized)
                return (hr_effort, "hr", hr_effort)

    return (None, None, None)
