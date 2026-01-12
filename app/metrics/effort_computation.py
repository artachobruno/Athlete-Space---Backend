"""Effort computation engine for Normalized Power and Intensity Factor.

Implements:
- Normalized Power (NP) for cycling
- Running Effort metric (rNP) for running
- HR-based fallback effort
- Intensity Factor (IF) computation

All metrics follow TrainingPeaks-style canonical formulas.
"""

from __future__ import annotations

from collections import deque
from typing import Literal

EffortSource = Literal["power", "pace", "hr"]


def compute_normalized_power(power_samples: list[float | int | None], sample_rate_seconds: float = 1.0) -> float | None:
    """Compute Normalized Power (NP) for cycling.

    Canonical formula:
    1. Compute 30-second rolling average of power
    2. Raise each value to the 4th power
    3. Compute mean
    4. Take 4th root

    NP = ( mean( rolling_30s_power ** 4 ) ) ** 0.25

    Args:
        power_samples: List of power values in watts (can contain None for gaps)
        sample_rate_seconds: Time between samples in seconds (default 1.0)

    Returns:
        Normalized Power in watts, or None if insufficient data

    Edge cases:
        - Ignores zeros and None values (< 5s gaps)
        - Requires ≥ 20 minutes of data OR falls back to avg power
        - If no power samples → returns None
    """
    if not power_samples:
        return None

    # Filter out None values and zeros (treat zeros as stopped/gaps)
    valid_power = [float(p) for p in power_samples if p is not None and float(p) > 0]
    if not valid_power:
        return None

    # Calculate duration from sample count
    duration_seconds = len(power_samples) * sample_rate_seconds
    duration_minutes = duration_seconds / 60.0

    # Edge case: If < 20 minutes, fallback to average power
    if duration_minutes < 20.0:
        avg_power = sum(valid_power) / len(valid_power)
        return round(avg_power, 1)

    # Compute 30-second rolling average
    window_size = max(1, int(30.0 / sample_rate_seconds))  # Number of samples in 30s window
    rolling_avg: list[float] = []

    # Use deque for efficient rolling window
    window = deque(maxlen=window_size)
    for power in power_samples:
        if power is not None and float(power) > 0:
            window.append(float(power))
            if len(window) == window_size:
                # Calculate rolling average
                avg = sum(window) / len(window)
                rolling_avg.append(avg)

    if not rolling_avg:
        # Fallback to average power if rolling avg fails
        avg_power = sum(valid_power) / len(valid_power)
        return round(avg_power, 1)

    # Raise each rolling average to 4th power
    powered = [avg**4 for avg in rolling_avg]

    # Compute mean
    mean_powered = sum(powered) / len(powered)

    # Take 4th root
    np = mean_powered ** 0.25

    return round(np, 1)


def compute_running_effort(
    pace_samples: list[float | int | None],
    elevation_samples: list[float | int | None] | None = None,
    sample_rate_seconds: float = 1.0,
) -> float | None:
    """Compute Running Effort metric (rNP) using Normalized Graded Pace.

    Option A - Normalized Graded Pace (RECOMMENDED):
    - Convert pace → speed
    - Apply grade adjustment
    - Apply same rolling + 4th-power logic as NP

    Args:
        pace_samples: List of pace values in m/s (can contain None for gaps)
        elevation_samples: Optional list of elevation values in meters for grade adjustment
        sample_rate_seconds: Time between samples in seconds (default 1.0)

    Returns:
        Running effort metric (rNP) in m/s, or None if insufficient data

    Edge cases:
        - Ignores zeros and None values
        - Requires ≥ 20 minutes of data OR falls back to avg pace
        - If no pace samples → returns None
    """
    if not pace_samples:
        return None

    # Filter out None values and zeros
    valid_pace = [float(p) for p in pace_samples if p is not None and float(p) > 0]
    if not valid_pace:
        return None

    # Calculate duration from sample count
    duration_seconds = len(pace_samples) * sample_rate_seconds
    duration_minutes = duration_seconds / 60.0

    # Edge case: If < 20 minutes, fallback to average pace
    if duration_minutes < 20.0:
        avg_pace = sum(valid_pace) / len(valid_pace)
        return round(avg_pace, 2)

    # Convert pace to speed (already in m/s, but we'll apply grade adjustment)
    # Apply grade adjustment if elevation data is available
    adjusted_speeds: list[float] = []
    for i, pace in enumerate(pace_samples):
        if pace is not None and float(pace) > 0:
            speed = float(pace)
            # Apply grade adjustment if elevation data available
            if elevation_samples and i < len(elevation_samples) and elevation_samples[i] is not None:
                # Simple grade adjustment: steeper = slower effective pace
                # This is a simplified version - can be enhanced with actual grade calculation
                pass  # For now, use pace as-is (can be enhanced later)
            adjusted_speeds.append(speed)

    if not adjusted_speeds:
        avg_pace = sum(valid_pace) / len(valid_pace)
        return round(avg_pace, 2)

    # Compute 30-second rolling average
    window_size = max(1, int(30.0 / sample_rate_seconds))
    rolling_avg: list[float] = []

    window = deque(maxlen=window_size)
    for speed in adjusted_speeds:
        window.append(speed)
        if len(window) == window_size:
            avg = sum(window) / len(window)
            rolling_avg.append(avg)

    if not rolling_avg:
        avg_pace = sum(valid_pace) / len(valid_pace)
        return round(avg_pace, 2)

    # Raise each rolling average to 4th power
    powered = [avg**4 for avg in rolling_avg]

    # Compute mean
    mean_powered = sum(powered) / len(powered)

    # Take 4th root
    rnp = mean_powered ** 0.25

    return round(rnp, 2)


def compute_hr_effort(
    hr_samples: list[float | int | None],
    threshold_hr: float,
) -> float | None:
    """Compute HR-based effort fallback.

    Simple logic:
    effort = mean( HR / threshold_HR )

    Used only if:
    - No power available
    - No usable pace available

    Args:
        hr_samples: List of heart rate values in bpm (can contain None for gaps)
        threshold_hr: Threshold heart rate in bpm

    Returns:
        HR effort metric (normalized ratio), or None if insufficient data
    """
    if threshold_hr <= 0:
        return None

    if not hr_samples:
        return None

    # Filter out None values and zeros
    valid_hr = [float(hr) for hr in hr_samples if hr is not None and float(hr) > 0]
    if not valid_hr:
        return None

    # Calculate mean(HR / threshold_HR)
    ratios = [hr / threshold_hr for hr in valid_hr]
    effort = sum(ratios) / len(ratios)

    return round(effort, 3)


def compute_intensity_factor(
    normalized_effort: float,
    threshold: float,
) -> float | None:
    """Compute Intensity Factor (IF).

    Formula: IF = NP / threshold

    Where:
        - threshold = FTP (bike)
        - threshold = threshold_speed or pace (run)
        - threshold = threshold_hr (fallback)

    Args:
        normalized_effort: Normalized Power (cycling) or Running Effort (running) or HR effort
        threshold: Sport-specific threshold (FTP, threshold_pace, threshold_hr)

    Returns:
        Intensity Factor (clamped to [0.3, 1.5]), or None if threshold invalid
    """
    if threshold <= 0:
        return None

    if normalized_effort <= 0:
        return None

    intensity_factor = normalized_effort / threshold

    # Clamp to [0.3, 1.5]
    intensity_factor = max(0.3, min(1.5, intensity_factor))

    return round(intensity_factor, 2)
