"""Tests for MODIFY → week execution.

Tests enforce that:
- Intent distribution is preserved
- Easy sessions modified first
- Long and quality preserved
- Non-destructive (originals remain)
- Shift collisions prevented
- Replace_day delegates correctly
"""

import copy
from datetime import UTC, date, datetime, timezone
from unittest.mock import patch

import pytest

from app.coach.tools.modify_week import modify_week
from app.db.models import PlannedSession
from app.plans.modify.week_types import WeekModification


@pytest.fixture
def sample_week_sessions():
    """Create a sample week of planned sessions.

    Week structure:
    - Monday: Easy 5mi
    - Tuesday: Easy 4mi
    - Wednesday: Easy 6mi
    - Thursday: Quality (threshold)
    - Friday: Easy 3mi
    - Saturday: Long 12mi
    - Sunday: Rest
    """
    week_start = date(2024, 1, 15)  # Monday
    sessions_data = [
        {"day": 0, "title": "Easy Run", "distance_mi": 5.0, "intent": "easy", "session_type": "easy"},
        {"day": 1, "title": "Easy Run", "distance_mi": 4.0, "intent": "easy", "session_type": "easy"},
        {"day": 2, "title": "Easy Run", "distance_mi": 6.0, "intent": "easy", "session_type": "easy"},
        {"day": 3, "title": "Threshold", "distance_mi": 8.0, "intent": "quality", "session_type": "threshold"},
        {"day": 4, "title": "Easy Run", "distance_mi": 3.0, "intent": "easy", "session_type": "easy"},
        {"day": 5, "title": "Long Run", "distance_mi": 12.0, "intent": "long", "session_type": "long"},
        {"day": 6, "title": "Rest", "distance_mi": None, "intent": "rest", "session_type": "rest"},
    ]

    sessions = []
    for data in sessions_data:
        session_date = datetime.combine(
            week_start.replace(day=week_start.day + data["day"]),
            datetime.min.time(),
        ).replace(tzinfo=UTC)

        session = PlannedSession(
            id=f"test-week-{data['day']}",
            user_id="test-user",
            athlete_id=1,
            date=session_date,
            type="Run",
            title=data["title"],
            distance_mi=data["distance_mi"],
            session_type=data["session_type"],
            intent=data["intent"],
            intensity=data["intent"],
            plan_type="race",
            source="planner_v2",
        )
        sessions.append(session)

    return sessions


@patch("app.coach.tools.modify_week.save_modified_sessions")
@patch("app.coach.tools.modify_week.get_planned_sessions_in_range")
def test_easy_first_reduction(mock_get, mock_save, sample_week_sessions):
    """Test that volume reduction modifies easy sessions first."""
    # Get original easy distances
    original_easy_total = sum(s.distance_mi or 0.0 for s in sample_week_sessions if s.intent == "easy")
    original_long = next(s.distance_mi for s in sample_week_sessions if s.intent == "long")
    original_quality = next(s.distance_mi for s in sample_week_sessions if s.intent == "quality")

    # Mock repository to return sample sessions
    mock_get.return_value = sample_week_sessions

    # Mock save to return modified sessions (capture what was passed)
    def mock_save_side_effect(original_sessions, modified_sessions, modification_reason):
        # Return the modified sessions with new IDs for tracking
        saved = []
        for mod_session in modified_sessions:
            saved_session = copy.deepcopy(mod_session)
            saved_session.id = f"{mod_session.id}-modified"
            saved.append(saved_session)
        return saved

    mock_save.side_effect = mock_save_side_effect

    modification = WeekModification(
        change_type="reduce_volume",
        start_date="2024-01-15",
        end_date="2024-01-21",
        percent=0.1,  # 10% reduction - small enough that easy can absorb it
        reason="test reduction",
    )

    result = modify_week(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is True
    modified_session_ids = result.get("modified_sessions", [])
    assert len(modified_session_ids) > 0

    # Get the saved sessions from mock call
    assert mock_save.called
    call_args = mock_save.call_args
    saved_sessions = call_args.kwargs["modified_sessions"]

    # Filter by intent
    new_easy_sessions = [s for s in saved_sessions if s.intent == "easy"]
    new_long_sessions = [s for s in saved_sessions if s.intent == "long"]
    new_quality_sessions = [s for s in saved_sessions if s.intent == "quality"]

    # Easy sessions should be reduced
    if new_easy_sessions:
        new_easy_total = sum(s.distance_mi or 0.0 for s in new_easy_sessions)
        # Easy should be reduced (approximately 10%)
        assert new_easy_total < original_easy_total
        assert abs(new_easy_total - original_easy_total * 0.9) < 1.0  # Allow some rounding

    # Long and quality should be preserved with small reduction
    if new_long_sessions:
        # With 10% reduction, easy should absorb it, so long should be unchanged
        assert abs(new_long_sessions[0].distance_mi - original_long) < 0.1
    if new_quality_sessions:
        # Quality should always be untouched
        assert abs(new_quality_sessions[0].distance_mi - original_quality) < 0.1


@patch("app.coach.tools.modify_week.save_modified_sessions")
@patch("app.coach.tools.modify_week.get_planned_sessions_in_range")
def test_non_destructive(mock_get, mock_save, sample_week_sessions):
    """Test that original sessions remain (non-destructive)."""
    original_ids = {s.id for s in sample_week_sessions}

    # Mock repository
    mock_get.return_value = sample_week_sessions

    def mock_save_side_effect(original_sessions, modified_sessions, modification_reason):
        saved = []
        for mod_session in modified_sessions:
            saved_session = copy.deepcopy(mod_session)
            saved_session.id = f"{mod_session.id}-modified"
            saved.append(saved_session)
        return saved

    mock_save.side_effect = mock_save_side_effect

    modification = WeekModification(
        change_type="reduce_volume",
        start_date="2024-01-15",
        end_date="2024-01-21",
        percent=0.1,
        reason="test",
    )

    result = modify_week(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is True

    # Verify save was called with original sessions (non-destructive)
    assert mock_save.called
    call_args = mock_save.call_args
    passed_original_sessions = call_args.kwargs["original_sessions"]
    passed_original_ids = {s.id for s in passed_original_sessions}
    assert passed_original_ids == original_ids

    # New IDs should be different from originals
    modified_ids = set(result.get("modified_sessions", []))
    assert len(modified_ids) > 0
    # New IDs should not overlap with originals
    assert original_ids.intersection(modified_ids) == set()


@patch("app.coach.tools.modify_week.get_planned_sessions_in_range")
def test_shift_collision_raises(mock_get, sample_week_sessions):
    """Test that shifting two sessions to the same date raises error."""
    mock_get.return_value = sample_week_sessions

    modification = WeekModification(
        change_type="shift_days",
        start_date="2024-01-15",
        end_date="2024-01-21",
        shift_map={
            "2024-01-15": "2024-01-17",  # Monday -> Wednesday
            "2024-01-16": "2024-01-17",  # Tuesday -> Wednesday (collision!)
        },
        reason="test collision",
    )

    result = modify_week(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    # Should fail validation
    assert result["success"] is False
    assert "collision" in result.get("error", "").lower() or "duplicate" in result.get("error", "").lower()


@patch("app.coach.tools.modify_week.modify_day")
@patch("app.coach.tools.modify_week.get_planned_sessions_in_range")
def test_replace_day_delegates(mock_get, mock_modify_day, sample_week_sessions):
    """Test that replace_day delegates to modify_day."""
    mock_get.return_value = sample_week_sessions
    mock_modify_day.return_value = {
        "success": True,
        "message": "Session modified successfully",
        "modified_session_id": "test-modified-id",
        "original_session_id": "test-week-0",
    }
    modification = WeekModification(
        change_type="replace_day",
        start_date="2024-01-15",
        end_date="2024-01-21",
        target_date="2024-01-15",  # Monday
        day_modification={
            "change_type": "adjust_distance",
            "value": 7.0,
            "reason": "test replace",
        },
        reason="test",
    )

    result = modify_week(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    # Should succeed (delegates to modify_day)
    assert result["success"] is True
    assert "modified_sessions" in result
    # Verify modify_day was called
    assert mock_modify_day.called


@patch("app.coach.tools.modify_week.save_modified_sessions")
@patch("app.coach.tools.modify_week.get_planned_sessions_in_range")
def test_long_run_preserved(mock_get, mock_save, sample_week_sessions):
    """Test that long run intent is preserved and distance stays above minimum."""
    mock_get.return_value = sample_week_sessions

    def mock_save_side_effect(original_sessions, modified_sessions, modification_reason):
        saved = []
        for mod_session in modified_sessions:
            saved_session = copy.deepcopy(mod_session)
            saved_session.id = f"{mod_session.id}-modified"
            saved.append(saved_session)
        return saved

    mock_save.side_effect = mock_save_side_effect

    # Large reduction that might affect long run
    modification = WeekModification(
        change_type="reduce_volume",
        start_date="2024-01-15",
        end_date="2024-01-21",
        percent=0.5,  # 50% reduction
        reason="large reduction",
    )

    result = modify_week(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is True

    # Get saved sessions from mock
    assert mock_save.called
    call_args = mock_save.call_args
    saved_sessions = call_args.kwargs["modified_sessions"]

    new_long = next((s for s in saved_sessions if s.intent == "long"), None)
    if new_long:
        # Long run should still exist
        assert new_long.intent == "long"
        # Distance should be at least minimum (8mi) if it was modified
        if new_long.distance_mi is not None:
            assert new_long.distance_mi >= 8.0  # MIN_LONG_DISTANCE_MILES


@patch("app.coach.tools.modify_week.save_modified_sessions")
@patch("app.coach.tools.modify_week.get_planned_sessions_in_range")
def test_volume_increase(mock_get, mock_save, sample_week_sessions):
    """Test volume increase works correctly."""
    original_easy_total = sum(s.distance_mi or 0.0 for s in sample_week_sessions if s.intent == "easy")

    mock_get.return_value = sample_week_sessions

    def mock_save_side_effect(original_sessions, modified_sessions, modification_reason):
        saved = []
        for mod_session in modified_sessions:
            saved_session = copy.deepcopy(mod_session)
            saved_session.id = f"{mod_session.id}-modified"
            saved.append(saved_session)
        return saved

    mock_save.side_effect = mock_save_side_effect

    modification = WeekModification(
        change_type="increase_volume",
        start_date="2024-01-15",
        end_date="2024-01-21",
        miles=5.0,  # Add 5 miles
        reason="test increase",
    )

    result = modify_week(
        user_id="test-user",
        athlete_id=1,
        modification=modification,
    )

    assert result["success"] is True

    # Get saved sessions from mock
    assert mock_save.called
    call_args = mock_save.call_args
    saved_sessions = call_args.kwargs["modified_sessions"]

    new_easy_sessions = [s for s in saved_sessions if s.intent == "easy"]
    if new_easy_sessions:
        new_easy_total = sum(s.distance_mi or 0.0 for s in new_easy_sessions)
        # Easy total should increase
        assert new_easy_total > original_easy_total
