"""Mandatory Unit Tests for Planning Invariants.

These tests must never be removed.
CI must fail if these tests fail.

🧪 These tests enforce all invariants.

ARCHITECTURAL COMMITMENT: TIME-BASED VALIDATION
===============================================
Tests validate TIME (minutes) as the primary planning currency.
Distance is derived and not validated in these tests.
"""

import pytest

from app.planning.errors import PlanningInvariantError
from app.planning.validate import validate_week_plan


def test_missing_long_run_raises():
    """Test that missing long run raises PlanningInvariantError."""
    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_week_plan(
            week_duration_target_minutes=480,  # 8 hours = 480 minutes
            race_type="5k",
            day_plans=[
                {"type": "easy", "intensity": "easy", "duration_minutes": 60} for _ in range(7)
            ],
        )
    assert "MISSING_LONG_RUN" in exc_info.value.details
    assert exc_info.value.code == "INVALID_WEEK"


def test_too_many_hard_days_raises():
    """Test that too many hard days raises PlanningInvariantError."""
    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_week_plan(
            week_duration_target_minutes=480,
            race_type="5k",
            day_plans=[
                (
                    {"type": "easy", "intensity": "hard", "duration_minutes": 60}
                    if i < 3
                    else {"type": "easy", "intensity": "easy", "duration_minutes": 60}
                )
                for i in range(7)
            ],
        )
    assert "TOO_MANY_HARD_DAYS" in exc_info.value.details
    assert exc_info.value.code == "INVALID_WEEK"


def test_adjacent_hard_days_raises():
    """Test that adjacent hard days raises PlanningInvariantError."""
    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_week_plan(
            week_duration_target_minutes=480,
            race_type="5k",
            day_plans=[
                {"type": "long", "intensity": "hard", "duration_minutes": 90} if i == 0
                else {"type": "easy", "intensity": "hard", "duration_minutes": 60} if i == 1
                else {"type": "easy", "intensity": "easy", "duration_minutes": 60}
                for i in range(7)
            ],
        )
    assert "ADJACENT_HARD_DAYS" in exc_info.value.details
    assert exc_info.value.code == "INVALID_WEEK"


def test_wrong_weekly_time_total_raises():
    """Test that wrong weekly time total raises PlanningInvariantError."""
    # Target is 480 minutes (8 hours), actual is 420 minutes (7 hours)
    # Difference is 60 minutes, > 2% tolerance of 9.6 minutes
    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_week_plan(
            week_duration_target_minutes=480,
            race_type="5k",
            day_plans=[
                {"type": "long", "intensity": "easy", "duration_minutes": 90} if i == 0
                else {"type": "easy", "intensity": "easy", "duration_minutes": 55}  # 90 + 6*55 = 420 minutes
                for i in range(7)
            ],
        )
    assert "INVALID_WEEKLY_TIME" in exc_info.value.details
    assert exc_info.value.code == "INVALID_WEEK"


def test_valid_week_plan_passes():
    """Test that a valid week plan passes validation."""
    # Valid plan: 1 long run, 2 hard days (spaced), correct duration
    # Target: 480 minutes (8 hours), tolerance: 480 * 0.02 = 9.6 minutes
    # Total: 90 + 70 + 60 + 60 + 60 + 70 + 70 = 480 minutes (exact)
    validate_week_plan(
        week_duration_target_minutes=480,
        race_type="5k",
        day_plans=[
            {"type": "long", "intensity": "easy", "duration_minutes": 90},  # Day 0: long run
            {"type": "easy", "intensity": "hard", "duration_minutes": 70},  # Day 1: hard
            {"type": "easy", "intensity": "easy", "duration_minutes": 60},  # Day 2: easy (gap)
            {"type": "easy", "intensity": "easy", "duration_minutes": 60},  # Day 3: easy
            {"type": "easy", "intensity": "hard", "duration_minutes": 60},  # Day 4: hard
            {"type": "easy", "intensity": "easy", "duration_minutes": 70},  # Day 5: easy
            {"type": "easy", "intensity": "easy", "duration_minutes": 70},  # Day 6: easy
            # Total: 480 minutes (exact match)
        ],
    )
    # Should not raise


def test_valid_week_plan_with_exact_time_passes():
    """Test that a week plan with exact time target passes."""
    # Target: 480 minutes, Actual: 480 minutes (exact)
    validate_week_plan(
        week_duration_target_minutes=480,
        race_type="5k",
        day_plans=[
            {"type": "long", "intensity": "easy", "duration_minutes": 90},
            {"type": "easy", "intensity": "hard", "duration_minutes": 70},
            {"type": "easy", "intensity": "easy", "duration_minutes": 60},
            {"type": "easy", "intensity": "easy", "duration_minutes": 60},
            {"type": "easy", "intensity": "hard", "duration_minutes": 60},
            {"type": "easy", "intensity": "easy", "duration_minutes": 70},
            {"type": "easy", "intensity": "easy", "duration_minutes": 70},
            # Total: 480 minutes (exact)
        ],
    )
    # Should not raise


def test_valid_week_plan_with_tolerance_passes():
    """Test that a week plan within 2% tolerance passes."""
    # Target: 480 minutes, tolerance: 480 * 0.02 = 9.6 minutes
    # Actual: 485 minutes (5 minute difference, < 9.6 minute tolerance)
    validate_week_plan(
        week_duration_target_minutes=480,
        race_type="5k",
        day_plans=[
            {"type": "long", "intensity": "easy", "duration_minutes": 95},  # +5 minutes
            {"type": "easy", "intensity": "hard", "duration_minutes": 70},
            {"type": "easy", "intensity": "easy", "duration_minutes": 60},
            {"type": "easy", "intensity": "easy", "duration_minutes": 60},
            {"type": "easy", "intensity": "hard", "duration_minutes": 60},
            {"type": "easy", "intensity": "easy", "duration_minutes": 70},
            {"type": "easy", "intensity": "easy", "duration_minutes": 70},
            # Total: 485 minutes (5 minute difference, within 9.6 minute tolerance)
        ],
    )
    # Should not raise
