"""Tests for validator functions.

Tests validate_macro_plan and validate_week_volume functions.
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.planner.enums import DayType, WeekFocus
from app.planner.errors import InvalidMacroPlanError
from app.planner.models import MacroWeek, PlannedSession
from app.planner.validators import validate_macro_plan, validate_week_volume


def test_validate_macro_plan_correct_count() -> None:
    """Test that macro plan with correct week count validates."""
    weeks = [
        MacroWeek(week_index=1, focus=WeekFocus.BASE, total_distance=40.0),
        MacroWeek(week_index=2, focus=WeekFocus.BUILD, total_distance=45.0),
        MacroWeek(week_index=3, focus=WeekFocus.BUILD, total_distance=50.0),
    ]
    # Should not raise
    validate_macro_plan(weeks, expected_weeks=3)


def test_validate_macro_plan_wrong_count() -> None:
    """Test that macro plan with wrong week count raises error."""
    weeks = [
        MacroWeek(week_index=1, focus=WeekFocus.BASE, total_distance=40.0),
        MacroWeek(week_index=2, focus=WeekFocus.BUILD, total_distance=45.0),
    ]
    with pytest.raises(InvalidMacroPlanError, match="Expected 3 weeks, got 2"):
        validate_macro_plan(weeks, expected_weeks=3)


def test_validate_macro_plan_wrong_index() -> None:
    """Test that macro plan with non-sequential indices raises error."""
    weeks = [
        MacroWeek(week_index=1, focus=WeekFocus.BASE, total_distance=40.0),
        MacroWeek(week_index=3, focus=WeekFocus.BUILD, total_distance=45.0),  # Missing week 2
    ]
    with pytest.raises(InvalidMacroPlanError, match="Week index mismatch: expected 2, got 3"):
        validate_macro_plan(weeks, expected_weeks=2)


def test_validate_week_volume_correct() -> None:
    """Test that week volume validation passes when volumes match."""
    sessions = [
        PlannedSession(
            week_index=1,
            day_index=0,
            day_type=DayType.EASY,
            distance=10.0,
            template_id="easy_1",
            description="Easy run",
        ),
        PlannedSession(
            week_index=1,
            day_index=2,
            day_type=DayType.QUALITY,
            distance=15.0,
            template_id="quality_1",
            description="Quality session",
        ),
        PlannedSession(
            week_index=1,
            day_index=4,
            day_type=DayType.LONG,
            distance=15.0,
            template_id="long_1",
            description="Long run",
        ),
    ]
    # Total: 40.0
    # Should not raise
    validate_week_volume(sessions, expected_total=40.0)


def test_validate_week_volume_mismatch() -> None:
    """Test that week volume validation fails when volumes don't match."""
    sessions = [
        PlannedSession(
            week_index=1,
            day_index=0,
            day_type=DayType.EASY,
            distance=10.0,
            template_id="easy_1",
            description="Easy run",
        ),
        PlannedSession(
            week_index=1,
            day_index=2,
            day_type=DayType.QUALITY,
            distance=15.0,
            template_id="quality_1",
            description="Quality session",
        ),
    ]
    # Total: 25.0, expected: 40.0
    with pytest.raises(InvalidMacroPlanError, match=r"Volume mismatch: expected 40\.0, got 25\.0"):
        validate_week_volume(sessions, expected_total=40.0)


def test_validate_week_volume_rounding_tolerance() -> None:
    """Test that week volume validation handles rounding correctly."""
    sessions = [
        PlannedSession(
            week_index=1,
            day_index=0,
            day_type=DayType.EASY,
            distance=10.333,
            template_id="easy_1",
            description="Easy run",
        ),
        PlannedSession(
            week_index=1,
            day_index=2,
            day_type=DayType.QUALITY,
            distance=15.333,
            template_id="quality_1",
            description="Quality session",
        ),
        PlannedSession(
            week_index=1,
            day_index=4,
            day_type=DayType.LONG,
            distance=14.334,
            template_id="long_1",
            description="Long run",
        ),
    ]
    # Total: 40.0 (rounded), expected: 40.0
    # Should not raise due to rounding tolerance
    validate_week_volume(sessions, expected_total=40.0)
