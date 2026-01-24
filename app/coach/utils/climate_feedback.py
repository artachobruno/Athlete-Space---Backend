"""Climate context integration for feedback generation.

Provides helper functions to inject climate context into activity feedback.
"""

from __future__ import annotations

from loguru import logger

from app.coach.tools.climate_context import (
    convert_activity_performance_for_conditions,
    get_activity_climate_context,
)


def get_climate_feedback_context(activity_id: str, sport: str, duration_min: float | None) -> str:
    """Get climate context string for feedback generation.

    If activity has climate data, returns formatted context string.
    Otherwise returns empty string.

    Args:
        activity_id: Activity UUID
        sport: Sport type
        duration_min: Activity duration in minutes (optional)

    Returns:
        Formatted climate context string for feedback, or empty string
    """
    try:
        climate_data = get_activity_climate_context(activity_id)
        if not climate_data or not climate_data.get("conditions_label"):
            return ""

        conditions_label = climate_data.get("conditions_label", "")
        heat_stress_raw = climate_data.get("heat_stress_index", 0.0)
        heat_stress = float(heat_stress_raw) if isinstance(heat_stress_raw, (int, float, str)) else 0.0

        if not heat_stress or heat_stress < 0.3:
            # Mild conditions - minimal mention
            return f"Conditions: {conditions_label}."

        # Significant heat stress - provide context
        context_parts = [f"Conditions: {conditions_label}."]

        # Add performance equivalency if applicable (using exact coach language)
        if sport in {"run", "ride"} and duration_min:
            if heat_stress < 0.40:
                # Mild/Warm - minimal mention
                pass
            elif heat_stress < 0.60:
                context_parts.append("Conditions added some environmental strain.")
            elif heat_stress < 0.75:
                context_parts.append("Heat meaningfully increased aerobic stress today.")
            else:
                context_parts.append("Heat and humidity significantly increased cardiovascular load.")

        return " ".join(context_parts)

    except Exception as e:
        logger.warning(f"[CLIMATE_FEEDBACK] Failed to get climate context for activity {activity_id}: {e}")
        return ""


def get_climate_aware_pace_context(
    activity_id: str,
    sport: str,
    observed_pace_sec_per_km: float,
    duration_min: float,
) -> str:
    """Get climate-aware pace context for feedback.

    Uses performance equivalency tool to provide context about pace adjustments.

    Args:
        activity_id: Activity UUID
        sport: Sport type
        observed_pace_sec_per_km: Observed pace in seconds per kilometer
        duration_min: Activity duration in minutes

    Returns:
        Formatted context string about pace equivalency, or empty string
    """
    result = ""
    try:
        climate_data = get_activity_climate_context(activity_id)
        if not climate_data:
            return ""

        heat_stress_raw = climate_data.get("heat_stress_index")
        if not heat_stress_raw:
            return ""
        heat_stress = float(heat_stress_raw) if isinstance(heat_stress_raw, (int, float, str)) else 0.0
        if heat_stress < 0.3:
            return ""

        # Get performance equivalency
        equivalency = convert_activity_performance_for_conditions(
            sport=sport,
            observed_pace_sec_per_km=observed_pace_sec_per_km,
            heat_stress_index=heat_stress,
            duration_min=duration_min,
        )

        adjustment_pct_raw = equivalency.get("adjustment_pct", 0.0)
        adjustment_pct = float(adjustment_pct_raw) if isinstance(adjustment_pct_raw, (int, float, str)) else 0.0
        if adjustment_pct < 2.0:  # Less than 2% adjustment
            return ""

        reason = equivalency.get("reason", "")

        # Use exact coach language
        # Only mention if meaningful adjustment (>= 2.0)
        result = f"{reason} Execution was appropriate given environmental load."

    except Exception as e:
        logger.warning(f"[CLIMATE_FEEDBACK] Failed to get pace context for activity {activity_id}: {e}")
        return ""
    else:
        return result
