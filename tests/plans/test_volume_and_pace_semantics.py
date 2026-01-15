"""Regression tests for volume and pace semantics.

Tests enforce the foundational rules:
- Distance is ALWAYS miles
- Pace is always numeric
- Volume is derived, never stored
- Race goal pace is the anchor
"""

import pytest

from app.plans.pace import estimate_pace, get_zone_from_pace
from app.plans.types import PaceMetrics, WorkoutMetrics
from app.plans.validators import validate_workout_metrics
from app.plans.volume import compute_weekly_volume_miles


def test_distance_is_miles_only():
    """Test that distance is always in miles."""
    metrics = WorkoutMetrics(primary="distance", distance_miles=10.0)
    validate_workout_metrics(metrics)
    assert metrics.distance_miles == 10.0
    assert metrics.primary == "distance"


def test_pace_requires_numeric_value():
    """Test that pace must have numeric value if present."""
    # This should raise a ValueError
    with pytest.raises(ValueError, match="pace_min_per_mile"):
        metrics = WorkoutMetrics(
            primary="distance",
            distance_miles=6.0,
            pace=PaceMetrics(zone="easy", pace_source="training_estimate"),
        )
        validate_workout_metrics(metrics)


def test_pace_with_numeric_value_passes():
    """Test that pace with numeric value passes validation."""
    metrics = WorkoutMetrics(
        primary="distance",
        distance_miles=6.0,
        pace=PaceMetrics(
            pace_min_per_mile=8.5,
            zone="easy",
            pace_source="training_estimate",
        ),
    )
    validate_workout_metrics(metrics)
    assert metrics.pace is not None
    assert metrics.pace.pace_min_per_mile == 8.5


def test_primary_distance_requires_distance_miles():
    """Test that primary='distance' requires distance_miles."""
    with pytest.raises(ValueError, match="distance_miles"):
        metrics = WorkoutMetrics(primary="distance")
        validate_workout_metrics(metrics)


def test_primary_duration_requires_duration_min():
    """Test that primary='duration' requires duration_min."""
    with pytest.raises(ValueError, match="duration_min"):
        metrics = WorkoutMetrics(primary="duration")
        validate_workout_metrics(metrics)


def test_estimate_pace_from_race_goal():
    """Test pace estimation from race goal pace."""
    race_pace = 8.0  # 8 min/mile marathon pace
    easy_pace = estimate_pace("easy", race_pace, pace_source="race_goal")

    assert easy_pace.pace_min_per_mile is not None
    assert easy_pace.pace_min_per_mile == 8.0 * 1.25  # easy = 1.25x race pace
    assert easy_pace.zone == "easy"
    assert easy_pace.pace_source == "race_goal"


def test_estimate_pace_all_zones():
    """Test pace estimation for all zones."""
    race_pace = 8.0  # 8 min/mile

    zones = ["recovery", "easy", "z2", "steady", "mp", "threshold", "tempo", "5k", "vo2max"]

    for zone in zones:
        pace_metrics = estimate_pace(zone, race_pace)
        assert pace_metrics.pace_min_per_mile is not None
        assert pace_metrics.pace_min_per_mile > 0
        assert pace_metrics.zone == zone


def test_estimate_pace_unknown_zone():
    """Test that unknown zone raises ValueError."""
    with pytest.raises(ValueError, match="Unknown zone"):
        estimate_pace("unknown_zone", 8.0)


def test_get_zone_from_pace():
    """Test reverse lookup of zone from pace."""
    race_pace = 8.0
    easy_pace = 8.0 * 1.25  # 10.0 min/mile

    zone = get_zone_from_pace(easy_pace, race_pace)
    assert zone == "easy"


def test_compute_weekly_volume_miles():
    """Test weekly volume computation in miles."""
    workouts = [
        {"metrics": WorkoutMetrics(primary="distance", distance_miles=5.0)},
        {"metrics": WorkoutMetrics(primary="distance", distance_miles=8.0)},
        {"metrics": WorkoutMetrics(primary="distance", distance_miles=12.0)},
    ]

    total = compute_weekly_volume_miles(workouts)
    assert total == 25.0


def test_compute_weekly_volume_ignores_duration_workouts():
    """Test that duration-based workouts are excluded from volume."""
    workouts = [
        {"metrics": WorkoutMetrics(primary="distance", distance_miles=5.0)},
        {"metrics": WorkoutMetrics(primary="duration", duration_min=60)},
        {"metrics": WorkoutMetrics(primary="distance", distance_miles=8.0)},
    ]

    total = compute_weekly_volume_miles(workouts)
    assert total == 13.0  # Only distance workouts counted


def test_compute_weekly_volume_handles_dict_format():
    """Test that volume computation handles dict format for backward compatibility."""
    workouts = [
        {"metrics": {"primary": "distance", "distance_miles": 5.0}},
        {"metrics": {"primary": "distance", "distance_miles": 8.0}},
    ]

    total = compute_weekly_volume_miles(workouts)
    assert total == 13.0


def test_marathon_pace_equals_race_pace():
    """Test that marathon pace (mp) equals race goal pace."""
    race_pace = 8.0
    mp_pace = estimate_pace("mp", race_pace)

    assert mp_pace.pace_min_per_mile == race_pace


def test_pace_zones_ordering():
    """Test that pace zones are ordered correctly (slower to faster)."""
    race_pace = 8.0

    recovery = estimate_pace("recovery", race_pace)
    easy = estimate_pace("easy", race_pace)
    threshold = estimate_pace("threshold", race_pace)
    vo2max = estimate_pace("vo2max", race_pace)

    # Recovery should be slowest
    assert recovery.pace_min_per_mile > easy.pace_min_per_mile
    # Easy should be slower than threshold
    assert easy.pace_min_per_mile > threshold.pace_min_per_mile
    # Threshold should be slower than vo2max
    assert threshold.pace_min_per_mile > vo2max.pace_min_per_mile
