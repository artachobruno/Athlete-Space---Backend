"""Sport type normalization utilities.

Maps Strava activity types to allowed database values.
"""

from __future__ import annotations


def normalize_sport_type(strava_type: str) -> str:
    """Normalize Strava activity type to allowed database values.

    Maps Strava activity types to: 'run', 'ride', 'swim', 'strength', 'walk', 'other'

    Args:
        strava_type: Strava activity type (e.g., 'Run', 'Ride', 'VirtualRide', etc.)

    Returns:
        Normalized sport type
    """
    type_lower = strava_type.lower() if strava_type else "other"

    # Map Strava types to normalized values
    sport_map: dict[str, str] = {
        "run": "run",
        "running": "run",
        "ride": "ride",
        "bike": "ride",
        "cycling": "ride",
        "virtualride": "ride",
        "ebikeride": "ride",
        "swim": "swim",
        "swimming": "swim",
        "walk": "walk",
        "walking": "walk",
        "weighttraining": "strength",
        "workout": "strength",
        "strength": "strength",
    }

    return sport_map.get(type_lower, "other")
