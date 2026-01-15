"""Target calculation utilities for workout steps.

This module calculates target values (pace, power, HR) based on:
- Step intensity zones (easy, tempo, threshold, vo2, etc.)
- User-specific thresholds (threshold_pace_ms, ftp_watts, threshold_hr)
"""

from __future__ import annotations

from app.db.models import UserSettings
from app.workouts.canonical import StepIntensity, StepTargetType


def calculate_target_from_intensity(
    intensity: StepIntensity,
    sport: str,
    user_settings: UserSettings | None,
) -> tuple[StepTargetType, float | None, float | None, float | None]:
    """Calculate target values from intensity zone and user thresholds.

    Maps intensity zones to target ranges based on sport-specific thresholds:
    - Running: Uses threshold_pace_ms to calculate pace targets
    - Cycling: Uses ftp_watts to calculate power targets
    - Other: Uses threshold_hr to calculate HR targets

    Intensity zone mappings:
    - easy: 60-70% of threshold
    - tempo: 85-95% of threshold
    - lt2: 95-100% of threshold
    - threshold: 100% of threshold (narrow range)
    - vo2: 105-115% of threshold
    - flow: 70-85% of threshold
    - rest: No target (recovery)

    Args:
        intensity: Step intensity zone
        sport: Sport type (run, bike, swim, etc.)
        user_settings: User settings with threshold configuration

    Returns:
        Tuple of (target_type, target_min, target_max, target_value)
        - target_type: Target metric type (pace, power, hr, or none)
        - target_min: Minimum target value
        - target_max: Maximum target value
        - target_value: Single target value (if range is narrow)
    """
    if not user_settings:
        return (StepTargetType.NONE, None, None, None)

    if intensity == StepIntensity.REST:
        return (StepTargetType.NONE, None, None, None)

    sport_lower = sport.lower()

    # Running: Calculate pace targets from threshold_pace_ms
    if sport_lower == "run":
        if not user_settings.threshold_pace_ms or user_settings.threshold_pace_ms <= 0:
            return (StepTargetType.NONE, None, None, None)

        threshold_pace_ms = user_settings.threshold_pace_ms

        if intensity == StepIntensity.EASY:
            # Easy: 60-70% of threshold pace (slower = higher pace value)
            min_pace_ms = threshold_pace_ms / 0.70  # Slower
            max_pace_ms = threshold_pace_ms / 0.60  # Even slower
            return (StepTargetType.PACE, min_pace_ms, max_pace_ms, None)

        if intensity == StepIntensity.FLOW:
            # Flow: 70-85% of threshold pace
            min_pace_ms = threshold_pace_ms / 0.85
            max_pace_ms = threshold_pace_ms / 0.70
            return (StepTargetType.PACE, min_pace_ms, max_pace_ms, None)

        if intensity == StepIntensity.TEMPO:
            # Tempo: 85-95% of threshold pace
            min_pace_ms = threshold_pace_ms / 0.95
            max_pace_ms = threshold_pace_ms / 0.85
            return (StepTargetType.PACE, min_pace_ms, max_pace_ms, None)

        if intensity == StepIntensity.LT2:
            # LT2: 95-100% of threshold pace
            min_pace_ms = threshold_pace_ms / 1.00
            max_pace_ms = threshold_pace_ms / 0.95
            return (StepTargetType.PACE, min_pace_ms, max_pace_ms, None)

        if intensity == StepIntensity.THRESHOLD:
            # Threshold: 100% ± 2% (narrow range)
            min_pace_ms = threshold_pace_ms / 1.02
            max_pace_ms = threshold_pace_ms / 0.98
            return (StepTargetType.PACE, min_pace_ms, max_pace_ms, threshold_pace_ms)

        if intensity == StepIntensity.VO2:
            # VO2: 105-115% of threshold pace (faster = lower pace value)
            min_pace_ms = threshold_pace_ms / 1.15  # Faster
            max_pace_ms = threshold_pace_ms / 1.05  # Even faster
            return (StepTargetType.PACE, min_pace_ms, max_pace_ms, None)

    # Cycling: Calculate power targets from FTP
    if sport_lower in {"bike", "ride", "cycling"}:
        if not user_settings.ftp_watts or user_settings.ftp_watts <= 0:
            return (StepTargetType.NONE, None, None, None)

        ftp = user_settings.ftp_watts

        if intensity == StepIntensity.EASY:
            # Easy: 50-65% of FTP
            return (StepTargetType.POWER, ftp * 0.50, ftp * 0.65, None)

        if intensity == StepIntensity.FLOW:
            # Flow: 65-80% of FTP
            return (StepTargetType.POWER, ftp * 0.65, ftp * 0.80, None)

        if intensity == StepIntensity.TEMPO:
            # Tempo: 80-90% of FTP
            return (StepTargetType.POWER, ftp * 0.80, ftp * 0.90, None)

        if intensity == StepIntensity.LT2:
            # LT2: 90-100% of FTP
            return (StepTargetType.POWER, ftp * 0.90, ftp * 1.00, None)

        if intensity == StepIntensity.THRESHOLD:
            # Threshold: 100% ± 5% (narrow range)
            return (StepTargetType.POWER, ftp * 0.95, ftp * 1.05, ftp)

        if intensity == StepIntensity.VO2:
            # VO2: 105-120% of FTP
            return (StepTargetType.POWER, ftp * 1.05, ftp * 1.20, None)

    # Other sports: Calculate HR targets from threshold_hr
    if not user_settings.threshold_hr or user_settings.threshold_hr <= 0:
        return (StepTargetType.NONE, None, None, None)

    threshold_hr = float(user_settings.threshold_hr)

    if intensity == StepIntensity.EASY:
        # Easy: 60-70% of threshold HR
        return (StepTargetType.HR, threshold_hr * 0.60, threshold_hr * 0.70, None)

    if intensity == StepIntensity.FLOW:
        # Flow: 70-85% of threshold HR
        return (StepTargetType.HR, threshold_hr * 0.70, threshold_hr * 0.85, None)

    if intensity == StepIntensity.TEMPO:
        # Tempo: 85-95% of threshold HR
        return (StepTargetType.HR, threshold_hr * 0.85, threshold_hr * 0.95, None)

    if intensity == StepIntensity.LT2:
        # LT2: 95-100% of threshold HR
        return (StepTargetType.HR, threshold_hr * 0.95, threshold_hr * 1.00, None)

    if intensity == StepIntensity.THRESHOLD:
        # Threshold: 100% ± 3% (narrow range)
        return (StepTargetType.HR, threshold_hr * 0.97, threshold_hr * 1.03, threshold_hr)

    if intensity == StepIntensity.VO2:
        # VO2: 100-110% of threshold HR
        return (StepTargetType.HR, threshold_hr * 1.00, threshold_hr * 1.10, None)

    # Default: no target
    return (StepTargetType.NONE, None, None, None)
