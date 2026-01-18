"""Trend computation.

Simple linear trend analysis for metrics over time.
"""

try:
    import numpy as np
except ImportError:
    np = None  # numpy not available, will use fallback calculation


def compute_trend(values: list[float]) -> dict:
    """Compute simple linear trend from a list of values.

    Args:
        values: List of numeric values over time (chronological order)

    Returns:
        Dictionary with:
        - direction: "up", "down", "flat", or "unknown"
        - slope: Linear slope of the trend
    """
    if len(values) < 3:
        return {"direction": "unknown", "slope": 0.0}

    # Use numpy if available, otherwise fall back to manual calculation
    if np is not None:
        # Use numpy for linear regression
        x = np.arange(len(values), dtype=float)
        y = np.array(values, dtype=float)

        slope = np.polyfit(x, y, 1)[0]
    else:
        # Fallback to simple calculation without numpy
        n = len(values)
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n

        numerator = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return {"direction": "flat", "slope": 0.0}

        slope = numerator / denominator

    # Determine direction based on slope
    if slope > 0.01:
        direction = "up"
    elif slope < -0.01:
        direction = "down"
    else:
        direction = "flat"

    return {
        "direction": direction,
        "slope": float(slope),
    }
