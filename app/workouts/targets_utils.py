"""Utilities for working with workout step targets JSONB.

Helper functions to convert between legacy format and new JSONB format,
and to extract values from the targets structure.
"""

from __future__ import annotations

from app.workouts.targets_schema import StepTargets


def get_duration_seconds(targets: dict) -> int | None:
    """Extract duration_seconds from targets JSONB.

    Args:
        targets: Targets JSONB dict

    Returns:
        Duration in seconds, or None if not time-based
    """
    if not targets or "duration" not in targets:
        return None

    duration = targets["duration"]
    if isinstance(duration, dict) and duration.get("type") == "time":
        return duration.get("seconds")
    return None


def get_distance_meters(targets: dict) -> int | None:
    """Extract distance_meters from targets JSONB.

    Args:
        targets: Targets JSONB dict

    Returns:
        Distance in meters, or None if not distance-based
    """
    if not targets or "duration" not in targets:
        return None

    duration = targets["duration"]
    if isinstance(duration, dict) and duration.get("type") == "distance":
        return duration.get("meters")
    return None


def get_target_metric(targets: dict) -> str | None:
    """Extract target_metric from targets JSONB.

    Args:
        targets: Targets JSONB dict

    Returns:
        Target metric type, or None if no target
    """
    if not targets or "target" not in targets:
        return None

    target = targets["target"]
    if isinstance(target, dict):
        return target.get("metric")
    return None


def get_target_min(targets: dict) -> float | None:
    """Extract target_min from targets JSONB.

    Args:
        targets: Targets JSONB dict

    Returns:
        Minimum target value, or None if not a range target
    """
    if not targets or "target" not in targets:
        return None

    target = targets["target"]
    if isinstance(target, dict) and "min" in target:
        min_val = target["min"]
        if isinstance(min_val, (int, float)):
            return float(min_val)
    return None


def get_target_max(targets: dict) -> float | None:
    """Extract target_max from targets JSONB.

    Args:
        targets: Targets JSONB dict

    Returns:
        Maximum target value, or None if not a range target
    """
    if not targets or "target" not in targets:
        return None

    target = targets["target"]
    if isinstance(target, dict) and "max" in target:
        max_val = target["max"]
        if isinstance(max_val, (int, float)):
            return float(max_val)
    return None


def get_target_value(targets: dict) -> float | None:
    """Extract target_value from targets JSONB.

    Args:
        targets: Targets JSONB dict

    Returns:
        Single target value, or None if not a single-value target
    """
    if not targets or "target" not in targets:
        return None

    target = targets["target"]
    if isinstance(target, dict) and "value" in target and "min" not in target:
        value = target["value"]
        if isinstance(value, (int, float)):
            return float(value)
    return None


def targets_to_legacy(targets: dict) -> dict[str, int | float | str | None]:
    """Convert targets JSONB to legacy format for backward compatibility.

    Args:
        targets: Targets JSONB dict

    Returns:
        Dict with legacy keys: duration_seconds, distance_meters, target_metric,
        target_min, target_max, target_value
    """
    try:
        step_targets = StepTargets.model_validate(targets)
        return step_targets.to_legacy()
    except Exception:
        # Fallback: try to extract directly
        return {
            "duration_seconds": get_duration_seconds(targets),
            "distance_meters": get_distance_meters(targets),
            "target_metric": get_target_metric(targets),
            "target_min": get_target_min(targets),
            "target_max": get_target_max(targets),
            "target_value": get_target_value(targets),
        }


def legacy_to_targets(
    duration_seconds: int | None = None,
    distance_meters: int | None = None,
    target_metric: str | None = None,
    target_min: float | None = None,
    target_max: float | None = None,
    target_value: float | None = None,
) -> dict:
    """Convert legacy format to targets JSONB.

    Args:
        duration_seconds: Duration in seconds
        distance_meters: Distance in meters
        target_metric: Target metric type
        target_min: Minimum target value
        target_max: Maximum target value
        target_value: Single target value

    Returns:
        Targets JSONB dict
    """
    step_targets = StepTargets.from_legacy(
        duration_seconds=duration_seconds,
        distance_meters=distance_meters,
        target_metric=target_metric,
        target_min=target_min,
        target_max=target_max,
        target_value=target_value,
    )
    return step_targets.model_dump_jsonb()
