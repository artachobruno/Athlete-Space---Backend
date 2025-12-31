"""Data quality assessment for training metrics.

This module provides deterministic assessment of data quality to gate
LLM coach and ensure metrics are only used when data is sufficient.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.metrics.training_load import DailyTrainingRow


def assess_data_quality(
    daily_rows: list[DailyTrainingRow],
) -> str:
    """Assess data quality from daily training rows.

    Args:
        daily_rows: List of daily training rows, ordered chronologically

    Returns:
        Data quality status: "ok" | "limited" | "insufficient"

    Rules:
        - <14 days → insufficient
        - Gaps >3 days in last 14 → limited
        - Otherwise → ok

    Notes:
        - Deterministic and predictable
        - Explicit outcomes
    """
    if not daily_rows:
        return "insufficient"

    # Parse dates
    dates = [datetime.fromisoformat(row["date"]).date() for row in daily_rows]
    if not dates:
        return "insufficient"

    # Check total days
    if len(dates) < 14:
        return "insufficient"

    # Check for gaps >3 days in last 14 days
    today = datetime.now(tz=timezone.utc).date()
    last_14_days_start = today - timedelta(days=14)

    # Filter to last 14 days
    recent_dates = [d for d in dates if d >= last_14_days_start]
    recent_dates.sort()

    if not recent_dates:
        return "insufficient"

    # Check for gaps >3 days
    for i in range(len(recent_dates) - 1):
        gap_days = (recent_dates[i + 1] - recent_dates[i]).days
        if gap_days > 3:
            return "limited"

    return "ok"
