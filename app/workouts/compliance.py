"""Pure math functions for workout compliance computation.

Deterministic, time-aligned compliance computation between planned workout steps
and executed activity samples. No LLM, no inference - pure mathematical comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.workouts.models import WorkoutStep
from app.workouts.targets_utils import get_target_max, get_target_metric, get_target_min, get_target_value


@dataclass
class StepComplianceResult:
    """Result of step compliance computation."""

    duration_seconds: int
    time_in_range_seconds: int
    overshoot_seconds: int
    undershoot_seconds: int
    pause_seconds: int
    compliance_pct: float


# Epsilon for speed-based pause detection (m/s)
SPEED_EPSILON = 0.1


def _is_paused(
    cadence: float | None,
    velocity: float | None,
) -> bool:
    """Check if sample represents a pause.

    Pause conditions:
    - cadence = 0 OR
    - speed < epsilon (velocity_smooth < SPEED_EPSILON)

    Args:
        cadence: Cadence value (rpm) or None
        velocity: Velocity value (m/s) or None

    Returns:
        True if sample is paused, False otherwise
    """
    if cadence is not None and cadence == 0:
        return True

    return velocity is not None and velocity < SPEED_EPSILON


def _map_target_metric_to_stream_key(target_metric: str) -> str | None:
    """Map target metric name to streams data key.

    Args:
        target_metric: Target metric name (e.g., "pace", "hr", "power")

    Returns:
        Streams data key name, or None if not available
    """
    mapping: dict[str, str] = {
        "pace": "velocity_smooth",
        "hr": "heartrate",
        "power": "watts",
    }
    return mapping.get(target_metric.lower())


def _convert_velocity_to_pace(velocity_m_per_s: float) -> float:
    """Convert velocity (m/s) to pace (min/km).

    Args:
        velocity_m_per_s: Velocity in meters per second

    Returns:
        Pace in minutes per kilometer
    """
    if velocity_m_per_s <= 0:
        return float("inf")
    return 1000.0 / (velocity_m_per_s * 60.0)


def _get_metric_value(
    streams_data: dict[str, list],
    target_metric: str,
    index: int,
) -> float | None:
    """Get metric value from streams data at given index.

    Handles metric name mapping and unit conversions.

    Args:
        streams_data: Activity streams data dictionary
        target_metric: Target metric name (e.g., "pace", "hr", "power")
        index: Index in the time series

    Returns:
        Metric value at index in target metric units, or None if not available
    """
    # Map target metric to streams key
    stream_key = _map_target_metric_to_stream_key(target_metric)
    if stream_key is None:
        return None

    metric_data = streams_data.get(stream_key)
    if metric_data is None or index >= len(metric_data):
        return None

    value = metric_data[index]
    if value is None:
        return None

    try:
        value_float = float(value)
    except (ValueError, TypeError):
        return None

    # Convert units if needed
    if target_metric.lower() == "pace":
        # Convert velocity (m/s) to pace (min/km)
        return _convert_velocity_to_pace(value_float)

    # For hr and power, return as-is (units match)
    return value_float


def _classify_sample(
    step: WorkoutStep,
    streams_data: dict[str, list],
    index: int,
) -> tuple[str, bool]:
    """Classify a single activity sample.

    Classifies sample as: in_range, overshoot, undershoot, or pause.

    Args:
        step: Workout step with target metrics
        streams_data: Activity streams data dictionary
        index: Index in the time series

    Returns:
        Tuple of (classification, is_paused)
        classification: "in_range", "overshoot", "undershoot", or "pause"
        is_paused: True if sample is paused
    """
    # Check for pause first
    cadence = _get_metric_value(streams_data, "cadence", index)
    velocity = _get_metric_value(streams_data, "velocity_smooth", index)

    if _is_paused(cadence, velocity):
        return ("pause", True)

    # Extract target data from targets JSONB (schema v2)
    targets = step.targets or {}
    target_metric = get_target_metric(targets)
    target_min = get_target_min(targets)
    target_max = get_target_max(targets)
    target_value = get_target_value(targets)

    # If no target metric, mark as in_range (completed)
    if not target_metric:
        return ("in_range", False)

    # Get metric value (with mapping and unit conversion)
    metric_value = _get_metric_value(streams_data, target_metric, index)
    if metric_value is None:
        # Missing metric data - treat as in_range (completed)
        return ("in_range", False)

    # Check against target range
    if target_min is not None and target_max is not None:
        # Range target
        if target_min <= metric_value <= target_max:
            return ("in_range", False)
        if metric_value > target_max:
            return ("overshoot", False)
        return ("undershoot", False)

    if target_value is not None:
        # Single value target (treat as exact match with small tolerance)
        tolerance = 0.01 * abs(target_value) if target_value != 0 else 0.01
        if abs(metric_value - target_value) <= tolerance:
            return ("in_range", False)
        if metric_value > target_value:
            return ("overshoot", False)
        return ("undershoot", False)

    # No target specified - mark as in_range (completed)
    return ("in_range", False)


def slice_activity_samples(
    streams_data: dict[str, list],
    window_start: int,
    window_end: int,
) -> dict[str, list]:
    """Slice activity samples by time window.

    Args:
        streams_data: Activity streams data dictionary
        window_start: Start time in seconds from activity start
        window_end: End time in seconds from activity start

    Returns:
        Sliced streams data dictionary with samples within [window_start, window_end)
    """
    if not streams_data:
        return {}

    time_series = streams_data.get("time", [])
    if not time_series:
        return {}

    # Find indices within window
    indices: list[int] = []
    for i, time_val in enumerate(time_series):
        if time_val is None:
            continue
        try:
            time_float = float(time_val)
            if window_start <= time_float < window_end:
                indices.append(i)
        except (ValueError, TypeError):
            continue

    # Slice all streams at these indices
    sliced: dict[str, list] = {}
    for stream_name, stream_data in streams_data.items():
        if stream_name == "time":
            # For time, use actual values
            sliced[stream_name] = [stream_data[i] for i in indices]
        else:
            # For other streams, slice by index
            sliced[stream_name] = [stream_data[i] if i < len(stream_data) else None for i in indices]

    return sliced


def compute_step_compliance(
    step: WorkoutStep,
    streams_data: dict[str, list],
    window_start: int,
    window_end: int,
) -> StepComplianceResult:
    """Compute compliance metrics for a workout step.

    Pure function that computes deterministic compliance metrics by:
    1. Slicing activity samples to the step's time window
    2. Classifying each sample (in_range, overshoot, undershoot, pause)
    3. Aggregating metrics

    Args:
        step: Workout step with target metrics
        streams_data: Activity streams data dictionary
        window_start: Start time in seconds from activity start
        window_end: End time in seconds from activity start

    Returns:
        StepComplianceResult with aggregated metrics

    Note:
        - If target metric is missing, compliance_pct = 1.0 (completed)
        - Paused time is excluded from compliance denominator
        - Duration is the window length (window_end - window_start)
    """
    window_duration = window_end - window_start

    # Slice samples to window
    sliced_data = slice_activity_samples(streams_data, window_start, window_end)

    if not sliced_data:
        # No samples in window - mark as completed
        return StepComplianceResult(
            duration_seconds=window_duration,
            time_in_range_seconds=window_duration,
            overshoot_seconds=0,
            undershoot_seconds=0,
            pause_seconds=0,
            compliance_pct=1.0,
        )

    time_series = sliced_data.get("time", [])
    if not time_series:
        # No time data - mark as completed
        return StepComplianceResult(
            duration_seconds=window_duration,
            time_in_range_seconds=window_duration,
            overshoot_seconds=0,
            undershoot_seconds=0,
            pause_seconds=0,
            compliance_pct=1.0,
        )

    # Classify each sample and track time-weighted metrics
    # Use actual time values to determine how long each sample represents
    # Each sample represents time from midpoint with previous to midpoint with next
    total_time = 0.0
    in_range_time = 0.0
    overshoot_time = 0.0
    undershoot_time = 0.0
    pause_time = 0.0

    for i in range(len(time_series)):
        curr_time = time_series[i]
        if curr_time is None:
            continue

        try:
            curr_time_float = float(curr_time)
        except (ValueError, TypeError):
            continue

        # Calculate time interval this sample represents
        if len(time_series) == 1:
            # Only one sample: represents entire window
            time_delta = float(window_duration)
        elif i == 0:
            # First sample: from window_start to midpoint between first and second
            next_time = time_series[i + 1]
            if next_time is not None:
                try:
                    next_time_float = float(next_time)
                    midpoint = (curr_time_float + next_time_float) / 2.0
                    time_delta = midpoint - float(window_start)
                except (ValueError, TypeError):
                    time_delta = 1.0
            else:
                time_delta = float(window_end) - float(window_start)
        elif i == len(time_series) - 1:
            # Last sample: from midpoint with previous to window_end
            prev_time = time_series[i - 1]
            if prev_time is not None:
                try:
                    prev_time_float = float(prev_time)
                    midpoint = (prev_time_float + curr_time_float) / 2.0
                    time_delta = float(window_end) - midpoint
                except (ValueError, TypeError):
                    time_delta = float(window_end) - curr_time_float
            else:
                time_delta = float(window_end) - curr_time_float
        else:
            # Middle sample: from midpoint with previous to midpoint with next
            prev_time = time_series[i - 1]
            next_time = time_series[i + 1]
            if prev_time is not None and next_time is not None:
                try:
                    prev_time_float = float(prev_time)
                    next_time_float = float(next_time)
                    start_midpoint = (prev_time_float + curr_time_float) / 2.0
                    end_midpoint = (curr_time_float + next_time_float) / 2.0
                    time_delta = end_midpoint - start_midpoint
                except (ValueError, TypeError):
                    time_delta = 1.0
            else:
                time_delta = 1.0

        if time_delta <= 0:
            time_delta = 1.0

        classification, is_paused = _classify_sample(step, sliced_data, i)

        total_time += time_delta

        if is_paused:
            pause_time += time_delta
        elif classification == "in_range":
            in_range_time += time_delta
        elif classification == "overshoot":
            overshoot_time += time_delta
        elif classification == "undershoot":
            undershoot_time += time_delta

    # Calculate compliance percentage
    # Exclude pause time from denominator
    active_time = total_time - pause_time
    if active_time > 0:
        compliance_pct = in_range_time / active_time
    else:
        # All time was paused - mark as completed
        compliance_pct = 1.0

    # Round to integers for seconds
    return StepComplianceResult(
        duration_seconds=int(window_duration),
        time_in_range_seconds=int(in_range_time),
        overshoot_seconds=int(overshoot_time),
        undershoot_seconds=int(undershoot_time),
        pause_seconds=int(pause_time),
        compliance_pct=compliance_pct,
    )
