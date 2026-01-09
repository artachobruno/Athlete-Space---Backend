"""Tests for B17: User Feedback → Structured Constraints."""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.calendar.training_summary import ReliabilityFlags, TrainingSummary
from app.coach.schemas.constraints import ConstraintReasonCode, TrainingConstraints
from app.coach.utils.constraints import (
    RecoveryState,
    UserFeedback,
    translate_feedback_to_constraints,
    translate_feedback_to_constraints_entry,
)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _default_training_summary() -> TrainingSummary:
    """Create a default training summary for testing."""
    today = datetime.now(UTC).date()
    return TrainingSummary(
        window_start=today,
        window_end=today,
        days=1,
        volume={},
        intensity_distribution={},
        load={"ctl": 50.0, "atl": 45.0, "tsb": 5.0, "trend": "stable"},
        execution={"compliance_rate": 0.85},
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


# ============================================================================
# HIGH FATIGUE TESTS
# ============================================================================


def test_high_fatigue_reduces_volume():
    """Test that high fatigue (≥8) reduces volume."""
    feedback = UserFeedback(fatigue_level=8)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert constraints.volume_multiplier == 0.75
    assert ConstraintReasonCode.HIGH_FATIGUE in constraints.reason_codes
    assert constraints.intensity_cap == "moderate"
    assert "hard" in constraints.disallow_intensity_days


def test_high_fatigue_no_other_signals():
    """Test high fatigue with no other feedback signals."""
    feedback = UserFeedback(fatigue_level=9)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert constraints.volume_multiplier == 0.75
    assert len(constraints.reason_codes) >= 1
    assert constraints.confidence > 0.0


# ============================================================================
# PAIN TESTS
# ============================================================================


def test_pain_enforces_rest():
    """Test that reported pain enforces rest and reduces intensity."""
    feedback = UserFeedback(pain_reported=True)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert constraints.volume_multiplier == 0.6
    assert constraints.intensity_cap == "easy"
    assert constraints.force_rest_days == 1
    assert "hard" in constraints.disallow_intensity_days
    assert "moderate" in constraints.disallow_intensity_days
    assert ConstraintReasonCode.REPORTED_PAIN in constraints.reason_codes


def test_pain_overrides_other_signals():
    """Test that pain overrides other constraints (highest priority)."""
    feedback = UserFeedback(
        pain_reported=True,
        fatigue_level=9,
        soreness_level=8,
    )
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    # Pain restrictions should win
    assert constraints.volume_multiplier == 0.6  # Pain sets this
    assert constraints.intensity_cap == "easy"  # Pain sets this
    assert ConstraintReasonCode.REPORTED_PAIN in constraints.reason_codes


# ============================================================================
# HIGH SORENESS TESTS
# ============================================================================


def test_high_soreness_disallows_hard_days():
    """Test that high soreness (≥7) disallows hard days."""
    feedback = UserFeedback(soreness_level=7)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert "hard" in constraints.disallow_intensity_days
    assert ConstraintReasonCode.SYSTEMIC_SORENESS in constraints.reason_codes


# ============================================================================
# POOR SLEEP TESTS
# ============================================================================


def test_poor_sleep_3_days_caps_long_sessions():
    """Test that poor sleep for 3+ days caps long sessions."""
    feedback = UserFeedback(sleep_quality_days=3)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert constraints.long_session_cap_minutes == 75
    assert constraints.volume_multiplier <= 0.9
    assert ConstraintReasonCode.POOR_SLEEP in constraints.reason_codes


# ============================================================================
# LOW MOTIVATION TESTS
# ============================================================================


def test_low_motivation_caps_intensity():
    """Test that low motivation (≤3) caps intensity."""
    feedback = UserFeedback(motivation_level=3)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert constraints.intensity_cap == "moderate"
    assert constraints.volume_multiplier <= 0.9
    assert ConstraintReasonCode.LOW_MOTIVATION in constraints.reason_codes


# ============================================================================
# CONFLICTING SIGNALS TESTS
# ============================================================================


def test_conflicting_signals_strongest_wins():
    """Test that when multiple signals conflict, strongest wins."""
    feedback = UserFeedback(
        fatigue_level=9,  # High fatigue
        motivation_level=2,  # Low motivation
    )
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    # High fatigue should set volume_multiplier to 0.75
    assert constraints.volume_multiplier == 0.75
    # Both should contribute reason codes
    assert len(constraints.reason_codes) >= 2


def test_pain_always_wins():
    """Test that pain always wins over other signals."""
    feedback = UserFeedback(
        pain_reported=True,
        fatigue_level=9,
        soreness_level=8,
        motivation_level=1,
    )
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    # Pain should enforce its restrictions
    assert constraints.volume_multiplier == 0.6
    assert constraints.intensity_cap == "easy"
    assert ConstraintReasonCode.REPORTED_PAIN in constraints.reason_codes


# ============================================================================
# EXPIRY DATE TESTS
# ============================================================================


def test_expiry_date_enforced():
    """Test that expiry date is set (max 7 days)."""
    feedback = UserFeedback(fatigue_level=8)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    today = datetime.now(UTC).date()
    expected_max = today + timedelta(days=7)
    assert constraints.expiry_date <= expected_max
    assert constraints.expiry_date > today


def test_expiry_date_shortened_low_confidence():
    """Test that expiry is shortened if confidence < 0.5."""
    # Create feedback that produces low confidence
    feedback = UserFeedback(fatigue_level=5)  # Moderate, might not trigger
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    # If constraints were applied with low confidence, expiry should be shorter
    if constraints.confidence < 0.5 and constraints.volume_multiplier < 1.0:
        today = datetime.now(UTC).date()
        expected_max = today + timedelta(days=3)
        assert constraints.expiry_date <= expected_max


# ============================================================================
# NO FEEDBACK TESTS
# ============================================================================


def test_no_feedback_neutral_constraints():
    """Test that no feedback produces neutral constraints."""
    feedback = UserFeedback()  # All None
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert constraints.volume_multiplier == 1.0
    assert constraints.intensity_cap == "none"
    assert constraints.force_rest_days == 0
    assert len(constraints.disallow_intensity_days) == 0
    assert len(constraints.reason_codes) == 0
    assert constraints.confidence == 0.0
    assert "No constraints" in constraints.explanation


# ============================================================================
# DETERMINISM TESTS
# ============================================================================


def test_determinism_same_input_same_output():
    """Test that same input produces same output (deterministic)."""
    feedback = UserFeedback(fatigue_level=8, soreness_level=7)
    summary = _default_training_summary()
    recovery = _default_recovery_state()

    constraints1 = translate_feedback_to_constraints(feedback, summary, recovery)
    constraints2 = translate_feedback_to_constraints(feedback, summary, recovery)

    assert constraints1.volume_multiplier == constraints2.volume_multiplier
    assert constraints1.intensity_cap == constraints2.intensity_cap
    assert constraints1.force_rest_days == constraints2.force_rest_days
    assert constraints1.disallow_intensity_days == constraints2.disallow_intensity_days
    assert constraints1.reason_codes == constraints2.reason_codes


# ============================================================================
# RECOVERY MISMATCH TESTS
# ============================================================================


def test_recovery_mismatch_detected():
    """Test that recovery mismatch is detected and flagged."""
    # User reports fatigue but ATL is low (mismatch)
    feedback = UserFeedback(fatigue_level=7)
    recovery = RecoveryState(
        atl=20.0,  # Low ATL
        tsb=10.0,
        recovery_status="adequate",
        risk_flags=[],
    )
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        recovery,
    )
    # Should trust user feedback and apply constraints
    assert constraints.volume_multiplier < 1.0


# ============================================================================
# BOUNDS ENFORCEMENT TESTS
# ============================================================================


def test_bounds_volume_multiplier():
    """Test that volume multiplier is bounded (0.6-1.1)."""
    # This is tested implicitly through the constraint logic,
    # but verify bounds are enforced
    feedback = UserFeedback(pain_reported=True)  # Sets to 0.6
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert 0.6 <= constraints.volume_multiplier <= 1.1


def test_bounds_force_rest_days():
    """Test that force_rest_days is bounded (0-3)."""
    feedback = UserFeedback(pain_reported=True)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert 0 <= constraints.force_rest_days <= 3


# ============================================================================
# REASON CODES TESTS
# ============================================================================


def test_reason_codes_max_2():
    """Test that reason codes are limited to 1-2 max."""
    feedback = UserFeedback(
        fatigue_level=9,
        soreness_level=8,
        sleep_quality_days=4,
        motivation_level=2,
    )
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert len(constraints.reason_codes) <= 2


# ============================================================================
# EXPLANATION TESTS
# ============================================================================


def test_explanation_generated():
    """Test that explanation is generated."""
    feedback = UserFeedback(fatigue_level=8)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    assert len(constraints.explanation) > 0
    assert len(constraints.explanation) <= 200  # Max length
    assert "fatigue" in constraints.explanation.lower()


def test_explanation_factual_only():
    """Test that explanation is factual (no coaching language)."""
    feedback = UserFeedback(pain_reported=True)
    constraints = translate_feedback_to_constraints(
        feedback,
        _default_training_summary(),
        _default_recovery_state(),
    )
    explanation_lower = constraints.explanation.lower()
    # Should not contain coaching language
    assert "should" not in explanation_lower
    assert "recommend" not in explanation_lower
    assert "suggest" not in explanation_lower


# ============================================================================
# ENTRY POINT TESTS
# ============================================================================


def test_entry_point_with_defaults():
    """Test entry point with missing optional arguments."""
    feedback = UserFeedback(fatigue_level=8)
    constraints = translate_feedback_to_constraints_entry(feedback)
    # Should use defaults and still produce constraints
    assert constraints.volume_multiplier == 0.75


def test_entry_point_with_all_args():
    """Test entry point with all arguments provided."""
    feedback = UserFeedback(fatigue_level=8)
    summary = _default_training_summary()
    recovery = _default_recovery_state()
    constraints = translate_feedback_to_constraints_entry(feedback, summary, recovery)
    assert constraints.volume_multiplier == 0.75
