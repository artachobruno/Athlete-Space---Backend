"""Read-only access to metric trends.

Trend analysis for training metrics over time.
"""

from datetime import date

from loguru import logger

from app.analysis.trends import compute_trend
from app.tools.read.metrics import get_training_metrics


def get_metric_trends(
    user_id: str,
    metric: str,
    dates: list[date],
) -> dict:
    """Get trend of a single metric over time.

    READ-ONLY: Trend of a single metric over time.
    One metric at a time, no forecasting, no decisions.

    Args:
        user_id: User ID
        metric: Metric name ("ctl", "atl", "tsb", "weekly_load")
        dates: List of dates to compute trend over (chronological order)

    Returns:
        Dictionary with trend direction and slope
    """
    logger.debug(
        f"Reading metric trends: user_id={user_id}, metric={metric}, dates={len(dates)}"
    )

    if metric not in {"ctl", "atl", "tsb", "weekly_load"}:
        raise ValueError(f"Invalid metric: {metric}. Must be one of: ctl, atl, tsb, weekly_load")

    values = []

    for d in dates:
        metrics = get_training_metrics(user_id, d)
        value = getattr(metrics, metric)
        values.append(value)

    return compute_trend(values)
