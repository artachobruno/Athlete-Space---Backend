"""Smoke tests for Phase 3 - Forecasting & Safety.

Tests that simulation, risk flags, and recommendations work.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.analysis.risk import compute_risk_flags
from app.analysis.simulation import simulate_training_load
from app.tools.interfaces import PlannedSession, TrainingMetrics
from app.tools.read.recommendations import recommend_no_change
from app.tools.read.risk import get_risk_flags
from app.tools.read.simulation import simulate_training_load_forward


def test_simulation_runs():
    """Test that load simulation works."""
    today = datetime.now(UTC).date()

    # Create mock planned sessions
    planned = [
        PlannedSession(
            id="1",
            date=today,
            sport="run",
            intensity="easy",
            target_load=50.0,
        ),
        PlannedSession(
            id="2",
            date=today + timedelta(days=1),
            sport="run",
            intensity="moderate",
            target_load=70.0,
        ),
    ]

    # Create mock current metrics
    metrics = TrainingMetrics(ctl=60.0, atl=55.0, tsb=5.0, weekly_load=300.0)

    result = simulate_training_load(planned, metrics, horizon_days=2)

    assert "projected_ctl" in result
    assert "projected_atl" in result
    assert "projected_tsb" in result
    assert len(result["projected_ctl"]) == 2
    assert len(result["projected_atl"]) == 2
    assert len(result["projected_tsb"]) == 2


def test_risk_flags_runs():
    """Test that risk flag computation works."""
    # Test with high fatigue risk
    projected_tsb = [-10.0, -20.0, -30.0]  # Goes below -25
    flags = compute_risk_flags(projected_tsb, completion_pct=0.8)
    assert isinstance(flags, list)
    assert len(flags) >= 1
    assert flags[0]["type"] == "high_fatigue"

    # Test with low compliance
    flags = compute_risk_flags([10.0, 15.0, 20.0], completion_pct=0.5)
    assert len(flags) >= 1
    assert flags[0]["type"] == "low_compliance"

    # Test with subjective fatigue
    flags = compute_risk_flags([10.0, 15.0], completion_pct=0.8, fatigue_scores=[5, 8, 9])
    assert len(flags) >= 1
    assert flags[0]["type"] == "subjective_fatigue"

    # Test with no flags
    flags = compute_risk_flags([10.0, 15.0, 20.0], completion_pct=0.8, fatigue_scores=[3, 4, 5])
    assert isinstance(flags, list)


def test_no_change_recommendation():
    """Test that no-change recommendation works."""
    r = recommend_no_change("Stable metrics")
    assert r["recommendation"] == "no_change"
    assert "reason" in r
    assert r["reason"] == "Stable metrics"


@pytest.mark.integration
def test_simulation_tool_runs(test_user_id):
    """Test that simulate_training_load_forward tool can be called."""
    user_id = test_user_id

    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=30)

    result = simulate_training_load_forward(user_id, start_date, end_date, horizon_days=7)
    assert "projected_ctl" in result
    assert "projected_atl" in result
    assert "projected_tsb" in result


@pytest.mark.integration
def test_risk_flags_tool_runs(test_user_id):
    """Test that get_risk_flags tool can be called."""
    user_id = test_user_id

    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=30)

    flags = get_risk_flags(user_id, start_date, end_date)
    assert isinstance(flags, list)
    # Each flag should have type, severity, and reason
    for flag in flags:
        assert "type" in flag
        assert "severity" in flag
        assert "reason" in flag
