"""Smoke tests for Phase 2 - Reality Reconciliation.

Tests that compliance and trend tools exist and can be called.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.analysis.compliance import compute_plan_compliance
from app.analysis.trends import compute_trend
from app.tools.read.compliance import get_plan_compliance
from app.tools.read.trends import get_metric_trends


def test_compliance_runs():
    """Test that plan compliance computation works."""
    # Empty inputs should return valid structure
    result = compute_plan_compliance([], [])
    assert "completion_pct" in result
    assert "planned_count" in result
    assert "completed_count" in result
    assert "missed_sessions" in result
    assert "load_delta" in result


def test_trend_runs():
    """Test that trend computation works."""
    # Test with simple values
    trend = compute_trend([1.0, 2.0, 3.0, 4.0])
    assert "direction" in trend
    assert "slope" in trend
    assert trend["direction"] == "up"

    # Test with flat trend
    trend_flat = compute_trend([1.0, 1.0, 1.0])
    assert trend_flat["direction"] == "flat"

    # Test with down trend
    trend_down = compute_trend([4.0, 3.0, 2.0, 1.0])
    assert trend_down["direction"] == "down"

    # Test with insufficient data
    trend_unknown = compute_trend([1.0])
    assert trend_unknown["direction"] == "unknown"


@pytest.mark.integration
def test_plan_compliance_tool_runs(test_user_id):
    """Test that get_plan_compliance tool can be called."""
    user_id = test_user_id

    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=30)

    result = get_plan_compliance(user_id, start_date, end_date)
    assert "completion_pct" in result
    assert isinstance(result["planned_count"], int)
    assert isinstance(result["completed_count"], int)


@pytest.mark.integration
def test_metric_trends_tool_runs(test_user_id):
    """Test that get_metric_trends tool can be called."""
    user_id = test_user_id

    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=i) for i in range(5, 0, -1)]

    trend = get_metric_trends(user_id, "ctl", dates)
    assert "direction" in trend
    assert "slope" in trend
