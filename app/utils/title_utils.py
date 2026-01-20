"""Activity title normalization utilities.

Converts generic Strava auto-generated titles to meaningful descriptive titles
based on activity metrics (distance, duration, sport).
"""

from __future__ import annotations


# Time-of-day prefixes used by Strava
_TIME_PREFIXES = frozenset({"morning", "lunch", "afternoon", "evening", "night"})

# Activity types used by Strava
_ACTIVITY_TYPES = frozenset({
    "run", "ride", "swim", "walk", "hike", "workout",
    "weight training", "yoga", "crossfit", "elliptical",
    "stair stepper", "rowing", "ski", "snowboard",
    "ice skate", "kayak", "surf", "windsurf", "kitesurf",
})

# Simple generic titles
_GENERIC_EXACT = frozenset({
    "run", "running", "ride", "cycling", "swim", "swimming",
    "activity", "workout", "exercise", "training",
})


def is_generic_strava_title(title: str | None) -> bool:
    """Check if title is a generic Strava-style auto-generated title.

    Strava generates titles like "Morning Run", "Lunch Ride", "Afternoon Swim".
    These should be replaced with more descriptive titles.

    Args:
        title: Title to check

    Returns:
        True if title is generic/auto-generated
    """
    if not title:
        return True

    title_lower = title.lower().strip()

    # Check for "Time Activity" pattern (e.g., "Morning Run", "Lunch Swim")
    for prefix in _TIME_PREFIXES:
        for activity in _ACTIVITY_TYPES:
            if title_lower == f"{prefix} {activity}":
                return True

    # Also catch simple generic titles
    return title_lower in _GENERIC_EXACT


def normalize_activity_title(
    strava_title: str | None,
    sport: str,
    distance_meters: float | None,
    duration_seconds: int | None,
) -> str:
    """Normalize an activity title, replacing generic Strava titles with descriptive ones.

    Args:
        strava_title: Original title from Strava (may be generic like "Morning Run")
        sport: Sport type (e.g., "run", "ride", "swim")
        distance_meters: Distance in meters (optional)
        duration_seconds: Duration in seconds (optional)

    Returns:
        Descriptive title string
    """
    # If title is not generic, keep it as-is
    if strava_title and not is_generic_strava_title(strava_title):
        return strava_title

    # Generate a descriptive title based on metrics
    sport_lower = (sport or "run").lower()
    distance_m = distance_meters or 0
    duration_sec = duration_seconds or 0

    distance_km = distance_m / 1000.0
    distance_mi = distance_km * 0.621371
    duration_min = duration_sec / 60.0

    if sport_lower in ("run", "running"):
        return _generate_run_title(distance_km, distance_mi, duration_min)
    elif sport_lower in ("ride", "cycling", "bike"):
        return _generate_ride_title(distance_km, distance_mi, duration_min)
    elif sport_lower in ("swim", "swimming"):
        return _generate_swim_title(distance_m, duration_min)
    elif sport_lower in ("walk", "walking"):
        return _generate_walk_title(distance_km, duration_min)
    elif sport_lower in ("hike", "hiking"):
        return _generate_hike_title(distance_km, duration_min)
    else:
        return _generate_generic_title(sport_lower, duration_min)


def _generate_run_title(distance_km: float, distance_mi: float, duration_min: float) -> str:
    """Generate title for running activities."""
    # Check for race distances first
    if 4.8 <= distance_km <= 5.2:
        return "5K Run"
    elif 9.8 <= distance_km <= 10.2:
        return "10K Run"
    elif 14.8 <= distance_km <= 15.2:
        return "15K Run"
    elif 20.5 <= distance_km <= 21.5:
        return "Half Marathon"
    elif 41.5 <= distance_km <= 43.0:
        return "Marathon"

    # Distance-based titles
    if distance_km >= 20:
        return f"Long Run ({distance_km:.0f}K)"
    elif distance_km >= 15:
        return f"Long Run ({distance_mi:.0f} mi)"
    elif distance_km >= 10:
        return f"Steady Run ({distance_km:.0f}K)"
    elif distance_km >= 5:
        return f"Easy Run ({distance_km:.1f}K)"
    elif distance_km >= 2:
        return "Short Run"
    elif duration_min >= 20:
        return f"Run ({duration_min:.0f} min)"
    else:
        return "Quick Run"


def _generate_ride_title(distance_km: float, distance_mi: float, duration_min: float) -> str:
    """Generate title for cycling activities."""
    if distance_km >= 100:
        return f"Century Ride ({distance_km:.0f}K)"
    elif distance_km >= 50:
        return f"Long Ride ({distance_km:.0f}K)"
    elif distance_km >= 30:
        return f"Ride ({distance_km:.0f}K)"
    elif distance_km >= 15:
        return f"Easy Ride ({distance_km:.0f}K)"
    elif duration_min >= 30:
        return f"Ride ({duration_min:.0f} min)"
    else:
        return "Quick Ride"


def _generate_swim_title(distance_m: float, duration_min: float) -> str:
    """Generate title for swimming activities."""
    if distance_m >= 3800:
        return "Iron Distance Swim"
    elif distance_m >= 1900:
        return "Half Iron Swim"
    elif distance_m >= 1500:
        return "Olympic Swim"
    elif distance_m >= 750:
        return "Sprint Swim"
    elif distance_m >= 400:
        return f"Swim ({distance_m:.0f}m)"
    elif duration_min >= 20:
        return f"Swim ({duration_min:.0f} min)"
    else:
        return "Quick Swim"


def _generate_walk_title(distance_km: float, duration_min: float) -> str:
    """Generate title for walking activities."""
    if distance_km >= 10:
        return f"Long Walk ({distance_km:.0f}K)"
    elif distance_km >= 5:
        return f"Walk ({distance_km:.1f}K)"
    elif duration_min >= 30:
        return f"Walk ({duration_min:.0f} min)"
    else:
        return "Short Walk"


def _generate_hike_title(distance_km: float, duration_min: float) -> str:
    """Generate title for hiking activities."""
    if distance_km >= 15:
        return f"Long Hike ({distance_km:.0f}K)"
    elif distance_km >= 8:
        return f"Hike ({distance_km:.0f}K)"
    elif duration_min >= 60:
        hours = duration_min / 60
        return f"Hike ({hours:.1f} hrs)"
    else:
        return "Short Hike"


def _generate_generic_title(sport: str, duration_min: float) -> str:
    """Generate title for other activity types."""
    sport_display = sport.replace("_", " ").title()
    if duration_min >= 60:
        hours = duration_min / 60
        return f"{sport_display} ({hours:.1f} hrs)"
    elif duration_min >= 10:
        return f"{sport_display} ({duration_min:.0f} min)"
    else:
        return sport_display
