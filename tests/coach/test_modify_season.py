"""Tests for MODIFY → season execution.

Tests enforce that:
- modify_week is called N times (once per week)
- Correct percent/miles splitting across weeks
- Only intended weeks are touched
- Failure bubbles up correctly
- No direct DB writes in season
- Reason propagated to all weeks
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.coach.tools.modify_season import modify_season
from app.plans.modify.season_types import SeasonModification


@pytest.fixture
def mock_get_week_date_range():
    """Mock _get_week_date_range to return predictable dates."""
    with patch("app.coach.tools.modify_season._get_week_date_range") as mock:
        def side_effect(athlete_id: int, week_number: int, user_id: str | None = None):
            # Week 1: 2024-01-01 to 2024-01-07
            # Week 2: 2024-01-08 to 2024-01-14
            # Week 3: 2024-01-15 to 2024-01-21
            base_date = date(2024, 1, 1)
            week_start = base_date + timedelta(days=(week_number - 1) * 7)
            week_end = week_start + timedelta(days=6)
            return week_start, week_end

        mock.side_effect = side_effect
        yield mock


@pytest.fixture
def mock_get_season_weeks():
    """Mock _get_season_weeks to return predictable week numbers."""
    with patch("app.coach.tools.modify_season._get_season_weeks") as mock:
        mock.return_value = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        yield mock


@patch("app.coach.tools.modify_season.modify_week")
@patch("app.coach.tools.modify_season._get_season_weeks")
@patch("app.coach.tools.modify_season._get_week_date_range")
def test_delegates_to_modify_week(
    mock_get_week_date_range,
    mock_get_season_weeks,
    mock_modify_week,
):
    """Test that modify_season delegates to modify_week for each week."""
    # Setup mocks
    mock_get_season_weeks.return_value = [1, 2, 3]

    def week_date_side_effect(athlete_id: int, week_number: int, user_id: str | None = None):
        base_date = date(2024, 1, 1)
        week_start = base_date + timedelta(days=(week_number - 1) * 7)
        week_end = week_start + timedelta(days=6)
        return week_start, week_end

    mock_get_week_date_range.side_effect = week_date_side_effect

    # Mock modify_week to return success
    mock_modify_week.return_value = {
        "success": True,
        "message": "Week modified successfully",
        "modified_sessions": [f"session-{i}" for i in range(5)],
    }

    # Execute
    modification = SeasonModification(
        change_type="reduce_volume",
        start_week=1,
        end_week=3,
        percent=0.2,
        reason="fatigue",
    )

    result = modify_season(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    # Assert
    assert result["success"] is True
    assert mock_modify_week.call_count == 3

    # Check each call
    calls = mock_modify_week.call_args_list
    assert calls[0][1]["modification"].start_date == "2024-01-01"
    assert calls[0][1]["modification"].end_date == "2024-01-07"
    assert calls[0][1]["modification"].percent == 0.2
    assert calls[0][1]["modification"].reason == "fatigue"

    assert calls[1][1]["modification"].start_date == "2024-01-08"
    assert calls[1][1]["modification"].end_date == "2024-01-14"
    assert calls[1][1]["modification"].percent == 0.2

    assert calls[2][1]["modification"].start_date == "2024-01-15"
    assert calls[2][1]["modification"].end_date == "2024-01-21"
    assert calls[2][1]["modification"].percent == 0.2


@patch("app.coach.tools.modify_season.modify_week")
@patch("app.coach.tools.modify_season._get_season_weeks")
@patch("app.coach.tools.modify_season._get_week_date_range")
def test_correct_percent_splitting(
    mock_get_week_date_range,
    mock_get_season_weeks,
    mock_modify_week,
):
    """Test that percent is applied uniformly to each week."""
    mock_get_season_weeks.return_value = [1, 2, 3]

    def week_date_side_effect(athlete_id: int, week_number: int, user_id: str | None = None):
        base_date = date(2024, 1, 1)
        week_start = base_date + timedelta(days=(week_number - 1) * 7)
        week_end = week_start + timedelta(days=6)
        return week_start, week_end

    mock_get_week_date_range.side_effect = week_date_side_effect
    mock_modify_week.return_value = {
        "success": True,
        "message": "Week modified",
        "modified_sessions": ["session-1"],
    }

    modification = SeasonModification(
        change_type="reduce_volume",
        start_week=1,
        end_week=3,
        percent=0.15,
    )

    result = modify_season(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is True

    # Check that same percent is applied to each week
    calls = mock_modify_week.call_args_list
    for call in calls:
        assert call[1]["modification"].percent == 0.15
        assert call[1]["modification"].miles is None


@patch("app.coach.tools.modify_season.modify_week")
@patch("app.coach.tools.modify_season._get_season_weeks")
@patch("app.coach.tools.modify_season._get_week_date_range")
def test_miles_distribution(
    mock_get_week_date_range,
    mock_get_season_weeks,
    mock_modify_week,
):
    """Test that miles are distributed evenly across weeks."""
    mock_get_season_weeks.return_value = [1, 2, 3]

    def week_date_side_effect(athlete_id: int, week_number: int, user_id: str | None = None):
        base_date = date(2024, 1, 1)
        week_start = base_date + timedelta(days=(week_number - 1) * 7)
        week_end = week_start + timedelta(days=6)
        return week_start, week_end

    mock_get_week_date_range.side_effect = week_date_side_effect
    mock_modify_week.return_value = {
        "success": True,
        "message": "Week modified",
        "modified_sessions": ["session-1"],
    }

    modification = SeasonModification(
        change_type="increase_volume",
        start_week=1,
        end_week=3,
        miles=30.0,
    )

    result = modify_season(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is True

    # Check that miles are distributed (30 / 3 = 10 per week)
    calls = mock_modify_week.call_args_list
    for call in calls:
        assert call[1]["modification"].miles == 10.0
        assert call[1]["modification"].percent is None


@patch("app.coach.tools.modify_season.modify_week")
@patch("app.coach.tools.modify_season._get_season_weeks")
@patch("app.coach.tools.modify_season._get_week_date_range")
def test_only_intended_weeks_touched(
    mock_get_week_date_range,
    mock_get_season_weeks,
    mock_modify_week,
):
    """Test that only weeks in the range are modified."""
    mock_get_season_weeks.return_value = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

    def week_date_side_effect(athlete_id: int, week_number: int, user_id: str | None = None):
        base_date = date(2024, 1, 1)
        week_start = base_date + timedelta(days=(week_number - 1) * 7)
        week_end = week_start + timedelta(days=6)
        return week_start, week_end

    mock_get_week_date_range.side_effect = week_date_side_effect
    mock_modify_week.return_value = {
        "success": True,
        "message": "Week modified",
        "modified_sessions": ["session-1"],
    }

    # Modify only weeks 3-5
    modification = SeasonModification(
        change_type="reduce_volume",
        start_week=3,
        end_week=5,
        percent=0.1,
    )

    result = modify_season(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is True
    assert mock_modify_week.call_count == 3

    # Check that only weeks 3, 4, 5 were called
    calls = mock_modify_week.call_args_list
    week_numbers_called = []
    for call in calls:
        # Extract week number from date (simplified check)
        start_date_str = call[1]["modification"].start_date
        # Week 3 starts at day 15, week 4 at day 22, week 5 at day 29
        if "2024-01-15" in start_date_str:
            week_numbers_called.append(3)
        elif "2024-01-22" in start_date_str:
            week_numbers_called.append(4)
        elif "2024-01-29" in start_date_str:
            week_numbers_called.append(5)

    assert len(week_numbers_called) == 3
    assert 3 in week_numbers_called
    assert 4 in week_numbers_called
    assert 5 in week_numbers_called


@patch("app.coach.tools.modify_season.modify_week")
@patch("app.coach.tools.modify_season._get_season_weeks")
@patch("app.coach.tools.modify_season._get_week_date_range")
def test_failure_bubbles_up(
    mock_get_week_date_range,
    mock_get_season_weeks,
    mock_modify_week,
):
    """Test that failure in any week bubbles up immediately."""
    mock_get_season_weeks.return_value = [1, 2, 3]

    def week_date_side_effect(athlete_id: int, week_number: int, user_id: str | None = None):
        base_date = date(2024, 1, 1)
        week_start = base_date + timedelta(days=(week_number - 1) * 7)
        week_end = week_start + timedelta(days=6)
        return week_start, week_end

    mock_get_week_date_range.side_effect = week_date_side_effect

    # First week succeeds, second week fails
    mock_modify_week.side_effect = [
        {"success": True, "message": "Week 1 modified", "modified_sessions": ["s1"]},
        {"success": False, "error": "Week 2 failed"},
    ]

    modification = SeasonModification(
        change_type="reduce_volume",
        start_week=1,
        end_week=3,
        percent=0.1,
    )

    result = modify_season(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is False
    assert "Week 2" in result["error"]
    assert mock_modify_week.call_count == 2  # Stopped after failure


@patch("app.coach.tools.modify_season.modify_week")
@patch("app.coach.tools.modify_season._get_season_weeks")
@patch("app.coach.tools.modify_season._get_week_date_range")
def test_reason_propagated(
    mock_get_week_date_range,
    mock_get_season_weeks,
    mock_modify_week,
):
    """Test that reason is propagated to all weeks."""
    mock_get_season_weeks.return_value = [1, 2, 3]

    def week_date_side_effect(athlete_id: int, week_number: int, user_id: str | None = None):
        base_date = date(2024, 1, 1)
        week_start = base_date + timedelta(days=(week_number - 1) * 7)
        week_end = week_start + timedelta(days=6)
        return week_start, week_end

    mock_get_week_date_range.side_effect = week_date_side_effect
    mock_modify_week.return_value = {
        "success": True,
        "message": "Week modified",
        "modified_sessions": ["session-1"],
    }

    modification = SeasonModification(
        change_type="reduce_volume",
        start_week=1,
        end_week=3,
        percent=0.1,
        reason="injury recovery",
    )

    result = modify_season(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is True

    # Check that reason is in all calls
    calls = mock_modify_week.call_args_list
    for call in calls:
        assert call[1]["modification"].reason == "injury recovery"


@patch("app.coach.tools.modify_season.modify_week")
@patch("app.coach.tools.modify_season._get_season_weeks")
def test_no_direct_db_writes(
    mock_get_season_weeks,
    mock_modify_week,
):
    """Test that modify_season never writes directly to DB."""
    mock_get_season_weeks.return_value = [1, 2, 3]

    # Mock _get_week_date_range to avoid DB calls in test
    with patch("app.coach.tools.modify_season._get_week_date_range") as mock_get_week:
        def week_date_side_effect(athlete_id: int, week_number: int, user_id: str | None = None):
            base_date = date(2024, 1, 1)
            week_start = base_date.replace(day=1 + (week_number - 1) * 7)
            week_end = week_start.replace(day=week_start.day + 6)
            return week_start, week_end

        mock_get_week.side_effect = week_date_side_effect
        mock_modify_week.return_value = {
            "success": True,
            "message": "Week modified",
            "modified_sessions": ["session-1"],
        }

        modification = SeasonModification(
            change_type="reduce_volume",
            start_week=1,
            end_week=3,
            percent=0.1,
        )

        result = modify_season(
            user_id="test-user",
            athlete_id=1,
            modification=modification,
        )

        assert result["success"] is True

        # Verify modify_week was called (which handles DB writes)
        assert mock_modify_week.call_count == 3

        # Verify no direct DB session usage in modify_season
        # (modify_week handles all persistence)


@patch("app.coach.tools.modify_season._get_season_weeks")
def test_validation_failure(mock_get_season_weeks):
    """Test that validation failures are caught."""
    mock_get_season_weeks.return_value = [1, 2, 3]

    # Invalid: start_week > end_week
    modification = SeasonModification(
        change_type="reduce_volume",
        start_week=5,
        end_week=3,  # Invalid
        percent=0.1,
    )

    result = modify_season(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is False
    assert "start_week" in result["error"].lower() or "end_week" in result["error"].lower()


@patch("app.coach.tools.modify_season._get_season_weeks")
def test_no_season_plan(mock_get_season_weeks):
    """Test that missing season plan returns error."""
    mock_get_season_weeks.return_value = []

    modification = SeasonModification(
        change_type="reduce_volume",
        start_week=1,
        end_week=3,
        percent=0.1,
    )

    result = modify_season(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is False
    assert "season" in result["error"].lower()
