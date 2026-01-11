"""Pace & Distance Resolver.

Converts time → distance safely and consistently.
Distance is ALWAYS derived from duration x pace.
"""


def derive_distance_miles(duration_min: int, pace_min_per_mile: float) -> float:
    """Derive distance from duration and pace.

    Distance is computed deterministically from time x pace.
    This function uses a single, consistent rounding policy.

    Args:
        duration_min: Duration in minutes
        pace_min_per_mile: Pace in minutes per mile

    Returns:
        Distance in miles, rounded to 2 decimal places

    Raises:
        ValueError: If pace is zero or negative
    """
    if pace_min_per_mile <= 0:
        raise ValueError(f"Invalid pace: {pace_min_per_mile} min/mile (must be positive)")

    distance = duration_min / pace_min_per_mile
    return round(distance, 2)
