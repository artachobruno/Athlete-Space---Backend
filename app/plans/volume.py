"""Weekly volume computation - miles-only.

This module computes weekly volume using ONLY miles.
No KM conversions. No mixed units.
"""

from app.plans.types import WorkoutMetrics


def compute_weekly_volume_miles(workouts: list[dict]) -> float:
    """Compute weekly volume in miles from workouts.

    Only sums distance-based workouts. Duration-based workouts are excluded
    from volume calculation (volume is distance-only in this system).

    Args:
        workouts: List of workout dictionaries with 'metrics' key containing WorkoutMetrics

    Returns:
        Total volume in miles
    """
    total_miles = 0.0

    for workout in workouts:
        metrics = workout.get("metrics")
        if not metrics:
            continue

        # Only count distance-based workouts
        if isinstance(metrics, WorkoutMetrics):
            if metrics.primary == "distance" and metrics.distance_miles is not None:
                total_miles += metrics.distance_miles
        elif isinstance(metrics, dict) and metrics.get("primary") == "distance" and metrics.get("distance_miles"):
            # Handle dict format for backward compatibility
            total_miles += float(metrics["distance_miles"])

    return round(total_miles, 2)
