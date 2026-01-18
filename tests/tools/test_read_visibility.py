"""Smoke test for Phase 1 read visibility tools.

Tests that all read-only tools exist and can be called (existence only).
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.tools.read.activities import get_completed_activities
from app.tools.read.calendar import get_calendar_events
from app.tools.read.metrics import get_training_metrics
from app.tools.read.plans import get_planned_activities
from app.tools.read.profile import get_athlete_profile


def test_read_visibility_tools_exist():
    """Test that all read-only tools are importable and callable."""
    # Test that functions exist
    assert get_completed_activities is not None
    assert get_planned_activities is not None
    assert get_athlete_profile is not None
    assert get_calendar_events is not None
    assert get_training_metrics is not None


@pytest.mark.integration
def test_read_visibility_tools_callable_with_valid_user(test_user_id):
    """Test that read tools can be called with a valid user_id.

    No assertions on values yet - existence only.
    """
    user_id = test_user_id

    # Test get_completed_activities
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    result_activities = get_completed_activities(user_id, start, end)
    assert result_activities is not None
    assert isinstance(result_activities, list)

    # Test get_planned_activities
    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=30)
    result_plans = get_planned_activities(user_id, start_date, end_date)
    assert result_plans is not None
    assert isinstance(result_plans, list)

    # Test get_athlete_profile
    result_profile = get_athlete_profile(user_id)
    assert result_profile is not None
    assert hasattr(result_profile, "athlete_id")

    # Test get_calendar_events
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    result_calendar = get_calendar_events(user_id, start, end)
    assert result_calendar is not None
    assert isinstance(result_calendar, list)

    # Test get_training_metrics
    today = datetime.now(UTC).date()
    result_metrics = get_training_metrics(user_id, today)
    assert result_metrics is not None
    assert hasattr(result_metrics, "ctl")
    assert hasattr(result_metrics, "atl")
    assert hasattr(result_metrics, "tsb")
    assert hasattr(result_metrics, "weekly_load")
