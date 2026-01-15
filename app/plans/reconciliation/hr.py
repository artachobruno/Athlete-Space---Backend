"""Heart rate zone mapping utilities.

This module provides the central HR → zone mapping function.
HR zones come from AthletePaceProfile.hr_zones.
"""


def map_hr_to_zone(hr: int, hr_profile: dict[str, dict[str, int]]) -> str:
    """Map heart rate to training zone.

    Args:
        hr: Heart rate in bpm
        hr_profile: HR zone profile dict, e.g., {"z1": {"min": 120, "max": 140}, ...}

    Returns:
        Zone name (e.g., "z1", "z2", "lt1", "lt2", "threshold") or "unknown" if no match
    """
    for zone, bounds in hr_profile.items():
        min_hr = bounds.get("min")
        max_hr = bounds.get("max")

        if min_hr is not None and max_hr is not None and min_hr <= hr < max_hr:
            return zone

    return "unknown"
