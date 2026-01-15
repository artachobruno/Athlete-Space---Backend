"""Centralized pace estimation logic - single source of truth.

This module provides the ONLY place where training pace zones are estimated
from race goal pace. No hard-coded paces anywhere else.

All pace calculations use race goal pace as the anchor and apply multipliers
to derive training zone paces.
"""

from typing import Literal

from app.plans.types import PaceMetrics

VALID_ZONES: tuple[str, ...] = (
    "recovery",
    "easy",
    "z1",
    "z2",
    "lt1",
    "lt2",
    "tempo",
    "steady",
    "mp",
    "hmp",
    "10k",
    "5k",
    "vo2max",
    "threshold",
)

# Pace multipliers relative to race goal pace (marathon pace = 1.00)
# Multipliers > 1.00 = slower than race pace
# Multipliers < 1.00 = faster than race pace
PACE_MULTIPLIERS: dict[str, float] = {
    "recovery": 1.35,
    "easy": 1.25,
    "z1": 1.25,
    "z2": 1.20,
    "steady": 1.10,
    "lt1": 1.05,
    "mp": 1.00,  # Marathon pace = race goal pace
    "lt2": 0.97,
    "threshold": 0.97,
    "tempo": 0.95,
    "hmp": 0.94,  # Half marathon pace
    "10k": 0.90,
    "5k": 0.86,
    "vo2max": 0.83,
}


def estimate_pace(
    zone: str,
    race_pace: float,
    pace_source: Literal["race_goal", "training_estimate", "hr_estimate"] = "training_estimate",
) -> PaceMetrics:
    """Estimate training pace from race goal pace and zone.

    This is the ONLY function that should be used to estimate training paces.
    All pace prescriptions must go through this function.

    Args:
        zone: Training zone (must be in PACE_MULTIPLIERS)
        race_pace: Race goal pace in minutes per mile
        pace_source: Source of the pace estimate (default: "training_estimate")

    Returns:
        PaceMetrics with numeric pace value and zone

    Raises:
        ValueError: If zone is not recognized
    """
    if zone not in PACE_MULTIPLIERS:
        raise ValueError(
            f"Unknown zone: {zone}. Valid zones: {list(PACE_MULTIPLIERS.keys())}"
        )

    if zone not in VALID_ZONES:
        raise ValueError(
            f"Invalid zone: {zone}. Valid zones: {VALID_ZONES}"
        )

    multiplier = PACE_MULTIPLIERS[zone]
    estimated_pace = race_pace * multiplier

    if zone == "recovery":
        valid_zone: Literal[
            "recovery", "easy", "z1", "z2", "lt1", "lt2", "tempo", "steady",
            "mp", "hmp", "10k", "5k", "vo2max", "threshold"
        ] = "recovery"
    elif zone == "easy":
        valid_zone = "easy"
    elif zone == "z1":
        valid_zone = "z1"
    elif zone == "z2":
        valid_zone = "z2"
    elif zone == "lt1":
        valid_zone = "lt1"
    elif zone == "lt2":
        valid_zone = "lt2"
    elif zone == "tempo":
        valid_zone = "tempo"
    elif zone == "steady":
        valid_zone = "steady"
    elif zone == "mp":
        valid_zone = "mp"
    elif zone == "hmp":
        valid_zone = "hmp"
    elif zone == "10k":
        valid_zone = "10k"
    elif zone == "5k":
        valid_zone = "5k"
    elif zone == "vo2max":
        valid_zone = "vo2max"
    elif zone == "threshold":
        valid_zone = "threshold"
    else:
        raise ValueError(f"Unexpected zone after validation: {zone}")

    return PaceMetrics(
        pace_min_per_mile=estimated_pace,
        pace_source=pace_source,
        zone=valid_zone,
    )


def get_zone_from_pace(
    pace_min_per_mile: float,
    race_pace: float,
) -> str | None:
    """Infer zone from pace value (reverse lookup).

    Finds the closest matching zone for a given pace.

    Args:
        pace_min_per_mile: Actual pace in minutes per mile
        race_pace: Race goal pace in minutes per mile

    Returns:
        Zone name or None if no close match
    """
    if race_pace <= 0:
        return None

    ratio = pace_min_per_mile / race_pace
    closest_zone = None
    min_diff = float("inf")

    for zone, multiplier in PACE_MULTIPLIERS.items():
        diff = abs(ratio - multiplier)
        if diff < min_diff:
            min_diff = diff
            closest_zone = zone

    # Only return if within reasonable tolerance (5%)
    if min_diff < 0.05:
        return closest_zone

    return None
