"""Coach tools for climate context and performance equivalency.

These tools read ONLY from activities table (never from sample tables).

Uses exact formulas (v1.0):
- Performance equivalency: equivalency_factor = 1 + min(0.15, heat_stress_index * 0.10)
- Max adjustment: +15%
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity
from app.db.session import get_session


def get_activity_climate_context(activity_id: str) -> dict[str, str | float | None]:
    """Get climate context for an activity.

    Reads ONLY from activities table (never from sample tables).

    Args:
        activity_id: Activity UUID

    Returns:
        Dictionary with climate context:
        - conditions_label: Human-readable conditions label
        - avg_temperature_c: Average temperature in Celsius
        - avg_dew_point_c: Average dew point in Celsius
        - heat_stress_index: Heat stress index (0.0-1.0)

        Returns empty dict if activity has no climate data.
    """
    with get_session() as session:
        activity = session.execute(select(Activity).where(Activity.id == activity_id)).scalar_one_or_none()

        if not activity:
            logger.warning(f"[CLIMATE] Activity {activity_id} not found")
            return {}

        if not activity.has_climate_data:
            logger.debug(f"[CLIMATE] Activity {activity_id} has no climate data")
            return {}

        # Use effective HSI if available (v1.1), otherwise fallback to raw HSI
        hsi_to_return = activity.effective_heat_stress_index
        if hsi_to_return is None:
            hsi_to_return = activity.heat_stress_index

        return {
            "conditions_label": activity.conditions_label,
            "avg_temperature_c": activity.avg_temperature_c,
            "avg_dew_point_c": activity.avg_dew_point_c,
            "heat_stress_index": activity.heat_stress_index,  # Always include raw HSI
            "effective_heat_stress_index": hsi_to_return,  # Use effective if available
            "heat_acclimation_score": activity.heat_acclimation_score,  # v1.1
            "wind_chill_c": activity.wind_chill_c,  # v2.0
            "cold_stress_index": activity.cold_stress_index,  # v2.0
        }


def convert_activity_performance_for_conditions(
    sport: str,
    observed_pace_sec_per_km: float,
    heat_stress_index: float,
    duration_min: float,
    effective_heat_stress_index: float | None = None,
) -> dict[str, float | str]:
    """Convert observed performance to equivalent performance accounting for conditions.

    Only applies to aerobic sessions (running, cycling).
    Adjustment cap: +15% maximum.
    Not applicable to races or intervals (v1).

    Args:
        sport: Sport type ('run', 'ride', etc.)
        observed_pace_sec_per_km: Observed pace in seconds per kilometer
        heat_stress_index: Raw heat stress index (0.0-1.0)
        duration_min: Activity duration in minutes
        effective_heat_stress_index: Effective HSI accounting for acclimation (v1.1, optional)

    Returns:
        Dictionary with:
        - equivalent_pace_sec_per_km: Equivalent pace in neutral conditions
        - adjustment_pct: Percentage adjustment applied
        - confidence: Confidence score (0.0-1.0)
        - reason: Human-readable explanation
    """
    # Only apply to aerobic sports
    if sport not in {"run", "ride"}:
        return {
            "equivalent_pace_sec_per_km": observed_pace_sec_per_km,
            "adjustment_pct": 0.0,
            "confidence": 0.0,
            "reason": "Performance equivalency only applies to aerobic sports (running, cycling)",
        }

    # v1.1: Use effective HSI if available, otherwise fallback to raw HSI
    hsi_for_calculation = effective_heat_stress_index if effective_heat_stress_index is not None else heat_stress_index

    # Heat stress adjustment model (exact formula v1.0)
    # Max adjustment: +15%
    equivalency_factor = 1.0 + min(0.15, hsi_for_calculation * 0.10)
    adjustment_pct = equivalency_factor - 1.0

    # Calculate equivalent pace (faster = lower seconds per km)
    # Equivalent pace is what the pace would be in neutral conditions
    equivalent_pace = observed_pace_sec_per_km / equivalency_factor

    # Confidence based on heat stress index and duration
    # Higher heat stress + longer duration = higher confidence
    # Normalize duration: use a factor based on duration (longer = higher confidence)
    duration_factor = min(1.0, duration_min / 60.0)  # Normalize to 1.0 for 60+ min
    confidence = min(1.0, hsi_for_calculation * 0.8 + (duration_factor * 0.2))

    # Generate reason using exact coach language mapping (v1.0)
    if hsi_for_calculation < 0.60:
        reason = "Conditions added some environmental strain."
    elif hsi_for_calculation < 0.75:
        reason = "Heat meaningfully increased aerobic stress today."
    else:
        reason = "Heat and humidity significantly increased cardiovascular load."

    logger.info(
        f"[CLIMATE] Performance conversion: sport={sport}, observed_pace={observed_pace_sec_per_km:.1f}s/km, "
        f"heat_stress={heat_stress_index:.2f}, effective_hsi={hsi_for_calculation:.2f}, "
        f"adjustment={adjustment_pct * 100:.1f}%, equivalent_pace={equivalent_pace:.1f}s/km"
    )

    return {
        "equivalent_pace_sec_per_km": round(equivalent_pace, 1),
        "adjustment_pct": round(adjustment_pct * 100, 1),
        "confidence": round(confidence, 2),
        "reason": reason,
    }
