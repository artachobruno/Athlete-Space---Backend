"""Volume repair engine for training plan generation.

This module provides deterministic volume repair logic to fix numeric drift
from LLM-generated training plans without requiring LLM regeneration.
"""

from loguru import logger

from app.planning.schema.session_spec import SessionSpec, SessionType


class RepairImpossibleError(Exception):
    """Raise when volume repair is impossible."""

    pass


class WeekVolumeMismatchError(Exception):
    """Raise when week volume mismatch cannot be repaired.

    This error should NOT trigger retries since LLM regeneration
    won't converge on numeric precision. Repair should be attempted first.
    """

    def __init__(self, message: str, target_km: float, actual_km: float, diff_km: float, tolerance_km: float):
        self.target_km = target_km
        self.actual_km = actual_km
        self.diff_km = diff_km
        self.tolerance_km = tolerance_km
        super().__init__(message)


def compute_week_volume(specs: list[SessionSpec]) -> float:
    """Compute total week volume from session specs.

    Args:
        specs: List of SessionSpec objects

    Returns:
        Total volume in kilometers
    """
    return sum(spec.target_distance_km or 0.0 for spec in specs)


def volume_within_tolerance(
    actual: float,
    target: float,
    tolerance: float = 0.05,
) -> bool:
    """Check if volume is within tolerance.

    Args:
        actual: Actual volume in km
        target: Target volume in km
        tolerance: Tolerance as fraction (default 0.05 = 5%)

    Returns:
        True if within tolerance, False otherwise
    """
    return abs(actual - target) <= target * tolerance


def repair_week_volume(
    specs: list[SessionSpec],
    target_km: float,
) -> list[SessionSpec]:
    """LEGACY REPAIR LOGIC DISABLED - Volume repair is not allowed in planner v2.

    This function is hard-disabled as part of B9. Volume is locked in B4.
    If volume is wrong, that is a hard failure, not a repair case.

    Raises:
        RuntimeError: Always, to prevent accidental usage
    """
    raise RuntimeError(
        "Legacy volume repair disabled. Use plan_race_simple from app.planner.plan_race_simple (planner v2). "
        "Volume is locked in B4 - if wrong, that is a hard failure."
    )
    actual = compute_week_volume(specs)

    if volume_within_tolerance(actual, target_km, tolerance=0.05):
        return specs

    scalable_types = {
        SessionType.EASY,
        SessionType.RECOVERY,
    }

    adjustable = [s for s in specs if s.session_type in scalable_types]

    if not adjustable:
        raise RepairImpossibleError("No adjustable sessions available for volume repair")

    scale = target_km / actual if actual > 0 else 1.0

    original_volumes = {}
    for spec in specs:
        if spec.target_distance_km:
            original_volumes[id(spec)] = spec.target_distance_km

    for spec in adjustable:
        if spec.target_distance_km is not None:
            spec.target_distance_km *= scale

    for spec in specs:
        if spec.session_type == SessionType.LONG and spec.target_distance_km is not None:
            original = original_volumes.get(id(spec), spec.target_distance_km)
            min_distance = original * 0.95
            max_distance = original * 1.05
            spec.target_distance_km = max(min_distance, min(max_distance, spec.target_distance_km))

    actual_after_scale = compute_week_volume(specs)
    delta = target_km - actual_after_scale

    if abs(delta) > 0.1 and adjustable:
        largest_adjustable = max(adjustable, key=lambda s: s.target_distance_km or 0.0)
        if largest_adjustable.target_distance_km is not None:
            largest_adjustable.target_distance_km += delta
            largest_adjustable.target_distance_km = max(0.0, largest_adjustable.target_distance_km)

    final_actual = compute_week_volume(specs)
    final_diff = abs(final_actual - target_km)

    logger.info(
        "Week volume repaired",
        target_km=target_km,
        original_km=actual,
        repaired_km=final_actual,
        scale=scale,
        final_diff_km=final_diff,
    )

    return specs
