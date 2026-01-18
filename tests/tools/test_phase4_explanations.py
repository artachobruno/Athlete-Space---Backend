"""Smoke tests for Phase 4 - Explanation & Trust.

Tests that rationale generation and decision audit logging work.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.explanations.rationale import generate_rationale
from app.tools.read.audit import get_recent_decisions
from app.tools.read.rationale import generate_plan_rationale
from app.tools.write.audit import record_decision_audit


def test_rationale_generation():
    """Test that rationale generation works."""
    context = {"user_id": "test_user", "date": datetime.now(UTC).date()}
    compliance = {
        "completion_pct": 0.85,
        "planned_count": 10,
        "completed_count": 8,
        "load_delta": 5.0,
    }
    trends = {"direction": "up", "slope": 2.5}
    risks = []
    recommendation = {"recommendation": "no_change", "reason": "Stable metrics"}

    r = generate_plan_rationale(context, compliance, trends, risks, recommendation)

    assert "summary" in r
    assert "key_factors" in r
    assert "what_went_well" in r
    assert "concerns" in r
    assert "recommendation" in r
    assert r["recommendation"] == recommendation


def test_rationale_with_risks():
    """Test rationale generation with risk flags."""
    context = {"user_id": "test_user"}
    compliance = {"completion_pct": 0.5}
    trends = {"direction": "down", "slope": -1.0}
    risks = [
        {"type": "high_fatigue", "severity": "high", "reason": "Projected TSB below -25"}
    ]
    recommendation = {"recommendation": "reduce_load", "reason": "High fatigue detected"}

    r = generate_plan_rationale(context, compliance, trends, risks, recommendation)

    assert len(r["concerns"]) > 0
    # The concerns list includes the risk reason, which should mention TSB
    assert any("tsb" in concern.lower() or "below" in concern.lower() for concern in r["concerns"])
    # The risk type is added to key_factors
    assert any("fatigue" in factor.lower() for factor in r["key_factors"])


@pytest.mark.integration
def test_decision_audit_write_and_read(test_user_id):
    """Test recording and retrieving decision audits."""
    user_id = test_user_id

    # Record a decision
    decision_type = "no_change"
    inputs = {
        "compliance": {"completion_pct": 0.9},
        "trends": {"direction": "flat"},
        "risks": [],
    }
    output = {"recommendation": "no_change", "reason": "Stable metrics"}
    rationale = {"summary": "All metrics stable", "concerns": []}

    record_decision_audit(user_id, decision_type, inputs, output, rationale)

    # Retrieve decisions
    logs = get_recent_decisions(user_id, limit=5)
    assert len(logs) > 0

    # Check the most recent decision
    recent = logs[0]
    assert recent["decision_type"] == decision_type
    assert recent["inputs"] == inputs
    assert recent["output"] == output
    assert recent["rationale"] == rationale


@pytest.mark.integration
def test_decision_audit_multiple_decisions(test_user_id):
    """Test that multiple decisions are properly logged and retrieved."""
    user_id = test_user_id

    # Record multiple decisions
    for i in range(3):
        record_decision_audit(
            user_id,
            f"decision_{i}",
            {"input": i},
            {"output": i},
            {"rationale": f"Test rationale {i}"},
        )

    # Retrieve decisions
    logs = get_recent_decisions(user_id, limit=10)
    assert len(logs) >= 3

    # Verify they're ordered by timestamp (most recent first)
    timestamps = [log["timestamp"] for log in logs]
    assert timestamps == sorted(timestamps, reverse=True)
