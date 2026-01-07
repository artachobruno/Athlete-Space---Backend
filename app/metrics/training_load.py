"""Training load metrics computation (CTL, ATL, TSB).

This module provides deterministic, idempotent computation of training load metrics
from daily training hours. All calculations use UTC timestamps and handle missing
data explicitly.

Metrics:
- CTL (Chronic Training Load): 42-day exponentially weighted moving average
- ATL (Acute Training Load): 7-day exponentially weighted moving average
- TSB (Training Stress Balance): CTL - ATL

Properties:
- Deterministic: Same input always produces same output
- Idempotent: Safe to recompute multiple times
- UTC-based: All timestamps in UTC, no timezone ambiguity
- Missing data handling: Explicit handling of gaps in training data
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import TypedDict


class DailyTrainingRow(TypedDict):
    """Daily training row from daily_training_summary table."""

    date: str  # ISO date string (YYYY-MM-DD)
    duration_s: int
    distance_m: float
    elevation_m: float
    load_score: float


def _normalize_to_scale(value: float, max_value: float = 100.0) -> float:
    """Normalize a metric value to -100 to 100 scale.

    Args:
        value: Metric value (typically 0-max_value)
        max_value: Maximum expected value for normalization (default 100)

    Returns:
        Normalized value in -100 to 100 range
    """
    normalized = (value / max_value) * 200.0 - 100.0
    return round(max(-100.0, min(100.0, normalized)), 2)


def calculate_ctl_atl_tsb(daily_load: list[float], normalize: bool = True) -> dict[str, list[float]]:
    """Calculate CTL, ATL, and TSB from daily training load.

    Args:
        daily_load: List of daily training hours (float), ordered chronologically.
                   Missing days should be represented as 0.0, not omitted.
        normalize: If True, normalize CTL and ATL to -100 to 100 scale (default: True)

    Returns:
        Dictionary with keys:
        - "ctl": List of CTL values (42-day EWMA, normalized to -100 to 100 if normalize=True)
        - "atl": List of ATL values (7-day EWMA, normalized to -100 to 100 if normalize=True)
        - "tsb": List of TSB values (CTL - ATL, also normalized if normalize=True)

    Algorithm:
        - CTL = 42-day exponentially weighted moving average
        - ATL = 7-day exponentially weighted moving average
        - TSB = CTL - ATL
        - If normalize=True, CTL and ATL are normalized to -100 to 100 scale

    Properties:
        - Deterministic: Same input produces same output
        - Idempotent: Safe to recompute
        - Handles missing data: Days with 0.0 load are treated as rest days

    Example:
        >>> daily_load = [1.0, 1.5, 0.0, 2.0, 1.0]
        >>> result = calculate_ctl_atl_tsb(daily_load)
        >>> len(result["ctl"]) == len(daily_load)
        True
    """
    if not daily_load:
        return {"ctl": [], "atl": [], "tsb": []}

    ctl = _calculate_ewma(daily_load, tau_days=42)
    atl = _calculate_ewma(daily_load, tau_days=7)

    if normalize:
        # Normalize CTL and ATL to -100 to 100 scale
        ctl = [_normalize_to_scale(c) for c in ctl]
        atl = [_normalize_to_scale(a) for a in atl]

    tsb = [round(c - a, 2) for c, a in zip(ctl, atl, strict=False)]

    return {"ctl": ctl, "atl": atl, "tsb": tsb}


def _calculate_ewma(values: list[float], tau_days: float) -> list[float]:
    """Calculate exponentially weighted moving average.

    Args:
        values: List of daily values (training hours)
        tau_days: Time constant in days (e.g., 42 for CTL, 7 for ATL)

    Returns:
        List of EWMA values, one per input value

    Formula:
        alpha = 1 - exp(-1 / tau)
        ewma[i] = alpha * value[i] + (1 - alpha) * ewma[i-1]
        ewma[0] = value[0] (or 0 if empty)

    Notes:
        - Uses e^(-1/tau) for exponential decay
        - First value initializes the EWMA
        - Missing days (0.0) are treated as rest days, not gaps
    """
    if not values:
        return []

    # Calculate smoothing factor
    # alpha = 1 - e^(-1/tau) where tau is the time constant in days
    alpha = 1 - math.exp(-1 / tau_days)

    result: list[float] = []
    prev = values[0] if values else 0.0

    for value in values:
        # EWMA: weighted average of current value and previous EWMA
        prev = alpha * value + (1 - alpha) * prev
        result.append(round(prev, 2))

    return result


def get_current_metrics(daily_load: list[float]) -> dict[str, float]:
    """Get current (most recent) CTL, ATL, and TSB values.

    Args:
        daily_load: List of daily training hours, ordered chronologically

    Returns:
        Dictionary with current values:
        - "ctl": Current CTL (float)
        - "atl": Current ATL (float)
        - "tsb": Current TSB (float)

    Returns zeros if no data available.
    """
    if not daily_load:
        return {"ctl": 0.0, "atl": 0.0, "tsb": 0.0}

    metrics = calculate_ctl_atl_tsb(daily_load)
    ctl_list = metrics["ctl"]
    atl_list = metrics["atl"]
    tsb_list = metrics["tsb"]

    if not ctl_list or not atl_list or not tsb_list:
        return {"ctl": 0.0, "atl": 0.0, "tsb": 0.0}

    return {
        "ctl": float(ctl_list[-1]),
        "atl": float(atl_list[-1]),
        "tsb": float(tsb_list[-1]),
    }


def compute_training_load(
    daily_rows: list[DailyTrainingRow],
    normalize: bool = True,
) -> dict[str, list[tuple[str, float]]]:
    """Compute CTL, ATL, and TSB from daily training rows.

    Args:
        daily_rows: List of daily training rows from daily_training_summary.
                   Rows should be ordered chronologically and include all days
                   in the requested range (missing days have zero values).
        normalize: If True, normalize CTL and ATL to -100 to 100 scale (default: True)

    Returns:
        Dictionary with keys:
        - "ctl": List of (date, value) tuples for CTL (normalized to -100 to 100 if normalize=True)
        - "atl": List of (date, value) tuples for ATL (normalized to -100 to 100 if normalize=True)
        - "tsb": List of (date, value) tuples for TSB

    Rules:
        - CTL = 42-day EWMA of load_score, normalized to -100 to 100 scale
        - ATL = 7-day EWMA of load_score, normalized to -100 to 100 scale
        - TSB = CTL - ATL
        - Missing days = load_score = 0
        - UTC only
        - Deterministic and recomputable anytime
    """
    if not daily_rows:
        return {"ctl": [], "atl": [], "tsb": []}

    # Convert daily_rows to a continuous series with explicit zeros for missing days
    # First, determine the date range
    dates = [datetime.fromisoformat(row["date"]).date() for row in daily_rows]
    if not dates:
        return {"ctl": [], "atl": [], "tsb": []}

    min_date = min(dates)
    max_date = max(dates)

    # Create a map of date -> load_score
    load_map: dict[date, float] = {datetime.fromisoformat(row["date"]).date(): row["load_score"] for row in daily_rows}

    # Build continuous series (fill gaps with 0.0)
    continuous_dates: list[date] = []
    daily_load: list[float] = []

    current_date = min_date
    while current_date <= max_date:
        continuous_dates.append(current_date)
        daily_load.append(load_map.get(current_date, 0.0))
        current_date += timedelta(days=1)

    # Calculate metrics (with normalization by default)
    metrics = calculate_ctl_atl_tsb(daily_load, normalize=normalize)

    # Convert to (date, value) tuples
    date_strings = [d.isoformat() for d in continuous_dates]

    return {
        "ctl": list(zip(date_strings, metrics["ctl"], strict=False)),
        "atl": list(zip(date_strings, metrics["atl"], strict=False)),
        "tsb": list(zip(date_strings, metrics["tsb"], strict=False)),
    }
