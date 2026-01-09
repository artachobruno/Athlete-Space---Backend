"""Tests for B18: Training Load Adjustment Tool.

Tests cover:
- High fatigue → volume reduction
- Conflicting signals → strongest constraint wins
- Low confidence → short window
- Bound violations blocked
- Determinism (same input twice)
- ATL/CTL ratio rules
- TSB rules
- Intensity cap rules
- Forced rest days computation
- Explanation generation
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.calendar.training_summary import KeySession, ReliabilityFlags, TrainingSummary
from app.coach.schemas.constraints import ConstraintReasonCode, TrainingConstraints
from app.coach.schemas.load_adjustment import AdjustmentReasonCode, LoadAdjustmentDecision
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.utils.constraints import RecoveryState

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _default_training_summary() -> TrainingSummary:
    """Create a default training summary for testing."""
    today = datetime.now(UTC).date()
    return TrainingSummary(
        window_start=today - timedelta(days=13),
        window_end=today,
        days=14,
        volume={"total_duration_minutes": 420, "total_distance_km": 50.0},
        intensity_distribution={"easy_pct": 60.0, "moderate_pct": 30.0, "hard_pct": 10.0},
        load={"ctl": 50.0, "atl": 45.0, "tsb": 5.0, "trend": "stable"},
        execution={"compliance_rate": 0.85, "completed_sessions": 10},
        anomalies=[],
        last_key_sessions=[],
        reliability_flags=ReliabilityFlags(
            low_compliance=False,
            high_variance=False,
            sparse_data=False,
        ),
    )


def _default_recovery_state() -> RecoveryState:
    """Create a default recovery state for testing."""
    return RecoveryState(
        atl=45.0,
        tsb=5.0,
        recovery_status="adequate",
        risk_flags=[],
    )


def _default_constraints() -> TrainingConstraints:
    """Create default constraints for testing."""
    return TrainingConstraints(
        volume_multiplier=1.0,
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )


# ============================================================================
# HIGH FATIGUE TESTS
# ============================================================================


def test_high_fatigue_reduces_volume():
    """Test that high fatigue constraint reduces volume."""
    constraints = TrainingConstraints(
        volume_multiplier=0.75,
        intensity_cap="moderate",
        force_rest_days=0,
        disallow_intensity_days={"hard"},
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[ConstraintReasonCode.HIGH_FATIGUE],
        explanation="High fatigue reported",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert decision.volume_delta_pct == -0.25  # 0.75 - 1.0
    assert decision.intensity_cap == "moderate"
    assert AdjustmentReasonCode.HIGH_FATIGUE in decision.reason_codes
    assert decision.confidence > 0.0


# ============================================================================
# ATL/CTL RATIO TESTS
# ============================================================================


def test_atl_ctl_ratio_caps_reduction():
    """Test that ATL/CTL ratio > 1.5 caps reduction at ≥ 20%."""
    summary = TrainingSummary(
        window_start=datetime.now(UTC).date() - timedelta(days=13),
        window_end=datetime.now(UTC).date(),
        days=14,
        volume={},
        intensity_distribution={},
        load={"ctl": 40.0, "atl": 70.0, "tsb": -30.0, "trend": "increasing"},  # ATL/CTL = 1.75
        execution={},
        anomalies=[],
        last_key_sessions=[],
        reliability_flags=ReliabilityFlags(
            low_compliance=False,
            high_variance=False,
            sparse_data=False,
        ),
    )

    recovery = RecoveryState(
        atl=70.0,
        tsb=-30.0,
        recovery_status="over",
        risk_flags=["ATL_SPIKE"],
    )

    constraints = TrainingConstraints(
        volume_multiplier=0.9,  # Would be -10% reduction
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=summary,
        recovery_state=recovery,
        constraints=constraints,
    )

    # Should be capped at -20% due to ATL/CTL ratio
    assert decision.volume_delta_pct <= -0.20
    assert AdjustmentReasonCode.ATL_SPIKE in decision.reason_codes


# ============================================================================
# TSB TESTS
# ============================================================================


def test_tsb_low_forces_reduction():
    """Test that TSB < -25 forces ≥ 15% reduction."""
    summary = TrainingSummary(
        window_start=datetime.now(UTC).date() - timedelta(days=13),
        window_end=datetime.now(UTC).date(),
        days=14,
        volume={},
        intensity_distribution={},
        load={"ctl": 50.0, "atl": 50.0, "tsb": -30.0, "trend": "stable"},
        execution={},
        anomalies=[],
        last_key_sessions=[],
        reliability_flags=ReliabilityFlags(
            low_compliance=False,
            high_variance=False,
            sparse_data=False,
        ),
    )

    recovery = RecoveryState(
        atl=50.0,
        tsb=-30.0,
        recovery_status="over",
        risk_flags=["TSB_LOW"],
    )

    constraints = _default_constraints()

    decision = adjust_training_load(
        training_summary=summary,
        recovery_state=recovery,
        constraints=constraints,
    )

    # Should force ≥ 15% reduction
    assert decision.volume_delta_pct <= -0.15
    assert AdjustmentReasonCode.TSB_LOW in decision.reason_codes


# ============================================================================
# HIGH VARIANCE TESTS
# ============================================================================


def test_high_variance_no_increase():
    """Test that high variance week → no increase allowed."""
    summary = TrainingSummary(
        window_start=datetime.now(UTC).date() - timedelta(days=13),
        window_end=datetime.now(UTC).date(),
        days=14,
        volume={},
        intensity_distribution={"easy_pct": 10.0, "moderate_pct": 10.0, "hard_pct": 80.0},
        load={"ctl": 50.0, "atl": 45.0, "tsb": 5.0, "trend": "stable"},
        execution={},
        anomalies=[],
        last_key_sessions=[],
        reliability_flags=ReliabilityFlags(
            low_compliance=False,
            high_variance=True,  # High variance
            sparse_data=False,
        ),
    )

    constraints = TrainingConstraints(
        volume_multiplier=1.1,  # Would be +10% increase
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=summary,
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    # Should cap at 0% (no increase)
    assert decision.volume_delta_pct <= 0.0
    assert AdjustmentReasonCode.HIGH_VARIANCE in decision.reason_codes


# ============================================================================
# INTENSITY CAP TESTS
# ============================================================================


def test_intensity_cap_from_constraints():
    """Test that intensity cap from constraints is respected."""
    constraints = TrainingConstraints(
        volume_multiplier=1.0,
        intensity_cap="moderate",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert decision.intensity_cap == "moderate"


def test_intensity_cap_from_poor_recovery():
    """Test that poor recovery status caps intensity at moderate."""
    recovery = RecoveryState(
        atl=50.0,
        tsb=-20.0,
        recovery_status="over",  # Poor recovery
        risk_flags=[],
    )

    constraints = _default_constraints()

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=recovery,
        constraints=constraints,
    )

    # Should cap at moderate if recovery is poor
    assert decision.intensity_cap in {"moderate", "easy"}


def test_intensity_cap_back_to_back_hard():
    """Test that back-to-back hard days caps intensity."""
    summary = TrainingSummary(
        window_start=datetime.now(UTC).date() - timedelta(days=13),
        window_end=datetime.now(UTC).date(),
        days=14,
        volume={},
        intensity_distribution={},
        load={"ctl": 50.0, "atl": 45.0, "tsb": 5.0, "trend": "stable"},
        execution={},
        anomalies=["High intensity clustered on back-to-back days"],
        last_key_sessions=[],
        reliability_flags=ReliabilityFlags(
            low_compliance=False,
            high_variance=False,
            sparse_data=False,
        ),
    )

    constraints = _default_constraints()

    decision = adjust_training_load(
        training_summary=summary,
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert decision.intensity_cap == "moderate"
    assert AdjustmentReasonCode.BACK_TO_BACK_HARD in decision.reason_codes


# ============================================================================
# FORCED REST DAYS TESTS
# ============================================================================


def test_forced_rest_days_computed():
    """Test that forced rest days are computed."""
    constraints = TrainingConstraints(
        volume_multiplier=1.0,
        intensity_cap="none",
        force_rest_days=2,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert len(decision.forced_rest_days) <= 2  # Bounded
    assert all(isinstance(day, str) for day in decision.forced_rest_days)
    # Dates should be ISO format (YYYY-MM-DD)
    for day in decision.forced_rest_days:
        datetime.fromisoformat(day)  # Should not raise


def test_forced_rest_days_avoid_key_sessions():
    """Test that forced rest days avoid key session dates."""
    today = datetime.now(UTC).date()
    key_session_date = today + timedelta(days=2)

    summary = TrainingSummary(
        window_start=today - timedelta(days=13),
        window_end=today,
        days=14,
        volume={},
        intensity_distribution={},
        load={"ctl": 50.0, "atl": 45.0, "tsb": 5.0, "trend": "stable"},
        execution={},
        anomalies=[],
        last_key_sessions=[
            KeySession(
                date=key_session_date.isoformat(),
                title="Long Run",
                status="planned",
                matched_activity_id=None,
            )
        ],
        reliability_flags=ReliabilityFlags(
            low_compliance=False,
            high_variance=False,
            sparse_data=False,
        ),
    )

    constraints = TrainingConstraints(
        volume_multiplier=1.0,
        intensity_cap="none",
        force_rest_days=1,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=today + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=summary,
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    # Forced rest day should not be on key session date
    if decision.forced_rest_days:
        for rest_day in decision.forced_rest_days:
            rest_date = datetime.fromisoformat(rest_day).date()
            assert rest_date != key_session_date


# ============================================================================
# WINDOW COMPUTATION TESTS
# ============================================================================


def test_window_default_7_days():
    """Test that default window is 7 days."""
    constraints = _default_constraints()

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert decision.effective_window_days <= 7
    assert decision.effective_window_days >= 1


def test_window_shrinks_with_expiry():
    """Test that window shrinks if expiry date is sooner."""
    today = datetime.now(UTC).date()
    constraints = TrainingConstraints(
        volume_multiplier=1.0,
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=today + timedelta(days=3),  # Expires in 3 days
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert decision.effective_window_days <= 3


def test_window_shrinks_low_confidence():
    """Test that window ≤ 3 days if confidence < 0.5."""
    constraints = TrainingConstraints(
        volume_multiplier=0.9,
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.3,  # Low confidence
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert decision.effective_window_days <= 3


# ============================================================================
# BOUNDS ENFORCEMENT TESTS
# ============================================================================


def test_volume_delta_bounded():
    """Test that volume_delta_pct is bounded [-0.40, +0.10]."""
    # Test minimum constraint (0.6 multiplier = -0.40 delta)
    constraints = TrainingConstraints(
        volume_multiplier=0.6,  # Minimum allowed, should produce -0.40 delta
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert -0.40 <= decision.volume_delta_pct <= 0.10
    # With 0.6 multiplier, should get -0.40 delta (clamped)
    assert decision.volume_delta_pct == -0.40


def test_forced_rest_days_bounded():
    """Test that forced_rest_days ≤ 2 per 7-day window."""
    # Use maximum allowed (3), but adjustment should cap at 2
    constraints = TrainingConstraints(
        volume_multiplier=1.0,
        intensity_cap="none",
        force_rest_days=3,  # Maximum allowed in constraints
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    # Adjustment logic should cap at 2 per window
    assert len(decision.forced_rest_days) <= 2


def test_long_session_cap_bounded():
    """Test that long_session_cap ≥ 45 min."""
    constraints = TrainingConstraints(
        volume_multiplier=1.0,
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=30,  # Below minimum
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    if decision.long_session_cap_minutes is not None:
        assert decision.long_session_cap_minutes >= 45


# ============================================================================
# DETERMINISM TESTS
# ============================================================================


def test_determinism_same_input_same_output():
    """Test that same input produces same output (deterministic)."""
    constraints = TrainingConstraints(
        volume_multiplier=0.8,
        intensity_cap="moderate",
        force_rest_days=1,
        disallow_intensity_days={"hard"},
        long_session_cap_minutes=90,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[ConstraintReasonCode.HIGH_FATIGUE],
        explanation="High fatigue",
        created_at=datetime.now(UTC),
    )

    summary = _default_training_summary()
    recovery = _default_recovery_state()

    decision1 = adjust_training_load(summary, recovery, constraints)
    decision2 = adjust_training_load(summary, recovery, constraints)

    assert decision1.volume_delta_pct == decision2.volume_delta_pct
    assert decision1.intensity_cap == decision2.intensity_cap
    assert decision1.long_session_cap_minutes == decision2.long_session_cap_minutes
    assert decision1.forced_rest_days == decision2.forced_rest_days
    assert decision1.effective_window_days == decision2.effective_window_days
    assert decision1.reason_codes == decision2.reason_codes
    assert decision1.confidence == decision2.confidence


# ============================================================================
# EXPLANATION TESTS
# ============================================================================


def test_explanation_generated():
    """Test that explanation is generated."""
    constraints = TrainingConstraints(
        volume_multiplier=0.75,
        intensity_cap="moderate",
        force_rest_days=1,
        disallow_intensity_days={"hard"},
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[ConstraintReasonCode.HIGH_FATIGUE],
        explanation="High fatigue",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert len(decision.explanation) > 0
    assert len(decision.explanation) <= 200
    assert "training" in decision.explanation.lower() or "load" in decision.explanation.lower()


def test_explanation_factual_only():
    """Test that explanation is factual (no advice language)."""
    constraints = _default_constraints()

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    explanation_lower = decision.explanation.lower()
    # Should not contain coaching language
    assert "should" not in explanation_lower
    assert "recommend" not in explanation_lower
    assert "suggest" not in explanation_lower
    assert "advice" not in explanation_lower


# ============================================================================
# NO CONSTRAINTS TESTS
# ============================================================================


def test_no_constraints_uses_defaults():
    """Test that no constraints uses defaults."""
    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=None,
    )

    # Should still produce a valid decision
    assert decision.volume_delta_pct == 0.0  # Default multiplier is 1.0
    assert decision.intensity_cap == "none"
    assert len(decision.forced_rest_days) == 0


# ============================================================================
# CONFIDENCE TESTS
# ============================================================================


def test_confidence_computed():
    """Test that confidence is computed."""
    constraints = TrainingConstraints(
        volume_multiplier=0.8,
        intensity_cap="moderate",
        force_rest_days=1,
        disallow_intensity_days={"hard"},
        long_session_cap_minutes=None,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.9,  # High confidence
        reason_codes=[ConstraintReasonCode.HIGH_FATIGUE],
        explanation="High fatigue",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert 0.0 <= decision.confidence <= 1.0
    assert decision.confidence > 0.0


# ============================================================================
# APPLIED CONSTRAINTS TESTS
# ============================================================================


def test_applied_constraints_tracked():
    """Test that applied constraints are tracked."""
    constraints = TrainingConstraints(
        volume_multiplier=0.8,
        intensity_cap="moderate",
        force_rest_days=1,
        disallow_intensity_days={"hard"},
        long_session_cap_minutes=90,
        expiry_date=datetime.now(UTC).date() + timedelta(days=7),
        source="user_feedback",
        confidence=0.8,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(UTC),
    )

    decision = adjust_training_load(
        training_summary=_default_training_summary(),
        recovery_state=_default_recovery_state(),
        constraints=constraints,
    )

    assert "volume_multiplier" in decision.applied_constraints
    assert "intensity_cap" in decision.applied_constraints
    assert "force_rest_days" in decision.applied_constraints
    assert "long_session_cap_minutes" in decision.applied_constraints
    assert "disallow_intensity_days" in decision.applied_constraints
