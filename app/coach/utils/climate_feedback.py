"""Climate context integration for feedback generation.

Provides helper functions to inject climate context into activity feedback.
"""

from __future__ import annotations

from loguru import logger

from app.coach.tools.climate_context import (
    convert_activity_performance_for_conditions,
    get_activity_climate_context,
)


def _resolve_heat_cold_priority(
    heat_stress: float,
    cold_stress: float | None,
) -> tuple[bool, bool]:
    """Resolve priority between heat and cold stress for feedback.

    Rules:
    - Heat → pacing interpretation
    - Cold → injury / stiffness guidance
    - Never stack aggressively

    Args:
        heat_stress: Heat stress index (0.0-1.0)
        cold_stress: Cold stress index (0.0-1.0) or None

    Returns:
        Tuple of (should_mention_heat, should_mention_cold)
    """
    if cold_stress is None or cold_stress < 0.20:
        # No meaningful cold stress - mention heat if significant
        return (heat_stress >= 0.30, False)

    if heat_stress < 0.30:
        # No meaningful heat stress - mention cold if significant
        return (False, cold_stress >= 0.20)

    # Both are significant - prioritize based on magnitude
    # If both are high, mention both but don't stack aggressively
    heat_dominant = heat_stress >= cold_stress

    if heat_dominant:
        # Heat is dominant - primary heat message, brief cold mention if very high
        return (True, cold_stress >= 0.60)
    # Cold is dominant - primary cold message, brief heat mention if very high
    return (heat_stress >= 0.60, True)


def get_climate_feedback_context(activity_id: str, sport: str, duration_min: float | None) -> str:
    """Get climate context string for feedback generation.

    If activity has climate data, returns formatted context string.
    Otherwise returns empty string.

    v2.0: Handles both heat and cold stress with priority resolution.

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
        # v1.1: Use effective HSI if available, otherwise raw HSI
        effective_hsi_raw = climate_data.get("effective_heat_stress_index")
        heat_stress_raw = climate_data.get("heat_stress_index", 0.0)
        heat_acclimation_score_raw = climate_data.get("heat_acclimation_score")

        # Determine which HSI to use
        hsi_to_use = effective_hsi_raw if effective_hsi_raw is not None else heat_stress_raw
        heat_stress = float(hsi_to_use) if isinstance(hsi_to_use, (int, float, str)) else 0.0
        heat_acclimation_score = (
            float(heat_acclimation_score_raw)
            if heat_acclimation_score_raw is not None and isinstance(heat_acclimation_score_raw, (int, float, str))
            else None
        )

        if not heat_stress or heat_stress < 0.3:
            # Mild conditions - minimal mention
            return f"Conditions: {conditions_label}."

        # Significant heat stress - provide context
        context_parts = [f"Conditions: {conditions_label}."]

        # v2.0: Get cold stress for priority resolution
        cold_stress_raw = climate_data.get("cold_stress_index")
        cold_stress = (
            float(cold_stress_raw)
            if cold_stress_raw is not None and isinstance(cold_stress_raw, (int, float, str))
            else None
        )

        # Resolve priority: Heat → pacing, Cold → injury/stiffness
        should_mention_heat, should_mention_cold = _resolve_heat_cold_priority(
            heat_stress=heat_stress,
            cold_stress=cold_stress,
        )

        # Heat feedback (pacing interpretation)
        if should_mention_heat:
            # v1.1: Add acclimation-aware language
            if heat_acclimation_score is not None:
                if heat_acclimation_score < 0.25:
                    # No acclimation mention
                    pass
                elif heat_acclimation_score < 0.60:
                    # Partial mitigation
                    context_parts.append("Heat impact was partially mitigated by recent exposure.")
                else:
                    # Strong adaptation
                    context_parts.append("You're adapting well to warm conditions.")

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

        # Cold feedback (injury/stiffness guidance)
        if should_mention_cold and cold_stress is not None:
            if cold_stress < 0.40:
                context_parts.append("Cold and wind likely increased muscular strain.")
            elif cold_stress < 0.60:
                context_parts.append("Cold conditions increased stiffness and injury risk.")
            else:
                context_parts.append("Severe cold significantly increased muscular strain and injury risk.")

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

        # v1.1: Use effective HSI if available
        effective_hsi_raw = climate_data.get("effective_heat_stress_index")
        heat_stress_raw = climate_data.get("heat_stress_index")
        if not heat_stress_raw:
            return ""

        heat_stress = float(heat_stress_raw) if isinstance(heat_stress_raw, (int, float, str)) else 0.0
        effective_hsi = (
            float(effective_hsi_raw)
            if effective_hsi_raw is not None and isinstance(effective_hsi_raw, (int, float, str))
            else None
        )

        # Use effective HSI for threshold check
        hsi_for_check = effective_hsi if effective_hsi is not None else heat_stress
        if hsi_for_check < 0.3:
            return ""

        # Get performance equivalency (pass effective HSI if available)
        equivalency = convert_activity_performance_for_conditions(
            sport=sport,
            observed_pace_sec_per_km=observed_pace_sec_per_km,
            heat_stress_index=heat_stress,
            duration_min=duration_min,
            effective_heat_stress_index=effective_hsi,
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
