"""Build coach context from overview payload.

This module builds structured context for the LLM coach from the
/me/overview API response. No raw activities are used, only structured JSON.
"""

from __future__ import annotations

from typing import Literal


def build_coach_context(overview_payload: dict) -> dict:
    """Build coach context from overview API payload.

    Args:
        overview_payload: Response from /me/overview endpoint

    Returns:
        Dictionary with structured context:
        {
            "data_quality": "ok" | "limited" | "insufficient",
            "metrics": {
                "ctl_today": float,
                "atl_today": float,
                "tsb_today": float,
                "tsb_7d_avg": float
            },
            "trends": {
                "ctl": "rising" | "stable" | "falling",
                "atl": "rising" | "stable" | "falling"
            }
        }

    Constraint:
        - No raw activities
        - No calculations here (all from API)
    """
    data_quality = overview_payload.get("data_quality", "insufficient")
    today = overview_payload.get("today", {})
    metrics_data = overview_payload.get("metrics", {})

    # Extract today's metrics
    ctl_today = today.get("ctl", 0.0)
    atl_today = today.get("atl", 0.0)
    tsb_today = today.get("tsb", 0.0)
    tsb_7d_avg = today.get("tsb_7d_avg", 0.0)

    # Calculate trends from metrics arrays
    ctl_trend = _calculate_trend(metrics_data.get("ctl", []))
    atl_trend = _calculate_trend(metrics_data.get("atl", []))

    return {
        "data_quality": data_quality,
        "metrics": {
            "ctl_today": float(ctl_today),
            "atl_today": float(atl_today),
            "tsb_today": float(tsb_today),
            "tsb_7d_avg": float(tsb_7d_avg),
        },
        "trends": {
            "ctl": ctl_trend,
            "atl": atl_trend,
        },
    }


def _calculate_trend(
    metric_array: list[tuple[str, float]],
) -> Literal["rising", "stable", "falling"]:
    """Calculate trend from metric array.

    Args:
        metric_array: List of (date, value) tuples

    Returns:
        "rising" | "stable" | "falling"
    """
    if not metric_array or len(metric_array) < 2:
        return "stable"

    # Compare last 7 days average vs previous 7 days average
    if len(metric_array) < 14:
        # If not enough data, compare last value vs first value
        first_val = metric_array[0][1]
        last_val = metric_array[-1][1]
        diff = last_val - first_val
    else:
        # Compare last 7 days vs previous 7 days
        last_7_avg = sum(val for _, val in metric_array[-7:]) / 7
        prev_7_avg = sum(val for _, val in metric_array[-14:-7]) / 7
        diff = last_7_avg - prev_7_avg

    # Threshold for "stable" is 2% change
    if abs(diff) < 0.02:
        return "stable"
    if diff > 0:
        return "rising"
    return "falling"
