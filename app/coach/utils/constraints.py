"""B17: User Feedback → Structured Constraints.

Converts subjective user feedback into explicit, bounded, machine-readable constraints
that downstream tools (planning + load adjustment) can safely consume.

DESIGN PRINCIPLES:
- No LLM usage
- Pure function
- Same input → same output
- Never adjusts training directly
- All effects must be explicit and bounded
- Safe defaults if feedback is vague
- Auditable (loggable)

INTEGRATION FLOW:

1. User provides feedback (text, sliders, ratings)
   ↓
2. Feedback parsing (normalize to UserFeedback)
   ↓
3. B16: Build TrainingSummary (execution, load, anomalies)
   ↓
4. B19: Compute RecoveryState (ATL, TSB, recovery_status)
   ↓
5. B17: translate_feedback_to_constraints() ← YOU ARE HERE
   ↓
6. TrainingConstraints (structured, bounded, expiring)
   ↓
7. Stored with decision log
   ↓
8. Consumed by:
   - B8 (planning) - applies volume_multiplier, intensity_cap, force_rest_days
   - B18 (load adjustment) - applies constraints to adjust load safely

USAGE EXAMPLE:

    from app.coach.utils.constraints import (
        UserFeedback,
        recovery_state_from_training_state,
        translate_feedback_to_constraints,
    )
    from app.calendar.training_summary import build_training_summary
    from app.state.builder import build_training_state

    # 1. Get user feedback (from text parsing or UI)
    feedback = UserFeedback(fatigue_level=8, pain_reported=True)

    # 2. Build TrainingSummary (B16)
    training_summary = build_training_summary(user_id, athlete_id)

    # 3. Build RecoveryState (from TrainingState or B19)
    training_state = build_training_state(...)
    recovery_state = recovery_state_from_training_state(training_state)

    # 4. Translate to constraints (B17)
    constraints = translate_feedback_to_constraints(
        feedback, training_summary, recovery_state
    )

    # 5. Store constraints (with decision log)
    # ... store logic ...

    # 6. Pass to B8 (planning) or B18 (load adjustment)
    # Both tools consume constraints but never call B17 directly
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger

from app.calendar.training_summary import ReliabilityFlags, TrainingSummary
from app.coach.schemas.constraints import ConstraintReasonCode, TrainingConstraints
from app.state.models import TrainingState

# ============================================================================
# INPUT MODELS
# ============================================================================


@dataclass
class UserFeedback:
    """User feedback signals (already parsed/normalized).

    This can come from:
    - Text parsing (existing logic)
    - Structured UI inputs (sliders, ratings)
    - Historical feedback aggregation
    """

    fatigue_level: int | None = None  # 0-10 scale
    soreness_level: int | None = None  # 0-10 scale
    pain_reported: bool = False
    motivation_level: int | None = None  # 0-10 scale
    sleep_quality_days: int | None = None  # Number of consecutive poor sleep days
    stress_level: int | None = None  # 0-10 scale


@dataclass
class RecoveryState:
    """Recovery state derived from training metrics.

    Can be extracted from TrainingState or computed separately.
    """

    atl: float  # Acute Training Load
    tsb: float  # Training Stress Balance
    recovery_status: Literal["under", "adequate", "over"]
    risk_flags: list[str]


def recovery_state_from_training_state(training_state: TrainingState) -> RecoveryState:
    """Extract RecoveryState from TrainingState.

    Args:
        training_state: TrainingState model

    Returns:
        RecoveryState extracted from TrainingState
    """
    return RecoveryState(
        atl=training_state.acute_load_7d,
        tsb=training_state.training_stress_balance,
        recovery_status=training_state.recovery_status,
        risk_flags=list(training_state.risk_flags),
    )


# ============================================================================
# CONSTRAINT TRANSLATION LOGIC (DETERMINISTIC)
# ============================================================================


def translate_feedback_to_constraints(
    feedback: UserFeedback,
    training_summary: TrainingSummary,
    recovery_state: RecoveryState,
) -> TrainingConstraints:
    """Translate feedback signals into training constraints.

    This is the core B17 logic: signals → constraints.

    Rules:
    - Pick the most restrictive constraint
    - Never stack multiplicatively
    - Never exceed bounds
    - Deterministic mapping

    Args:
        feedback: User feedback signals
        training_summary: Training summary (B16)
        recovery_state: Recovery state (B19)

    Returns:
        TrainingConstraints with derived constraints
    """
    constraints = TrainingConstraints(
        volume_multiplier=1.0,
        intensity_cap="none",
        force_rest_days=0,
        disallow_intensity_days=set(),
        long_session_cap_minutes=None,
        expiry_date=_calculate_expiry_date(),
        source="user_feedback",
        confidence=0.0,
        reason_codes=[],
        explanation="",
        created_at=datetime.now(timezone.utc),
    )

    reason_codes: list[ConstraintReasonCode] = []
    applied_constraints: list[str] = []

    # Map signals to constraints using deterministic rules
    # Order matters: most restrictive first

    # Priority 1: Pain (highest priority - always wins)
    if feedback.pain_reported:
        constraints.volume_multiplier = 0.6
        constraints.intensity_cap = "easy"
        constraints.force_rest_days = 1
        constraints.disallow_intensity_days.add("hard")
        constraints.disallow_intensity_days.add("moderate")
        reason_codes.append(ConstraintReasonCode.REPORTED_PAIN)
        applied_constraints.append("pain_restriction")

    # Priority 2: High fatigue (≥8 on 0-10 scale)
    if feedback.fatigue_level is not None and feedback.fatigue_level >= 8:
        constraints.volume_multiplier = min(constraints.volume_multiplier, 0.75)
        if not applied_constraints:
            constraints.intensity_cap = "moderate"
            constraints.disallow_intensity_days.add("hard")
        reason_codes.append(ConstraintReasonCode.HIGH_FATIGUE)
        applied_constraints.append("high_fatigue")

    # Priority 3: High soreness (≥7 on 0-10 scale)
    if feedback.soreness_level is not None and feedback.soreness_level >= 7:
        if "pain_restriction" not in applied_constraints:
            constraints.disallow_intensity_days.add("hard")
            if not applied_constraints:
                constraints.intensity_cap = "moderate"
        reason_codes.append(ConstraintReasonCode.SYSTEMIC_SORENESS)
        applied_constraints.append("high_soreness")

    # Priority 4: Poor sleep (3+ consecutive days)
    if feedback.sleep_quality_days is not None and feedback.sleep_quality_days >= 3:
        if "pain_restriction" not in applied_constraints:
            constraints.long_session_cap_minutes = 75
            constraints.volume_multiplier = min(constraints.volume_multiplier, 0.9)
        reason_codes.append(ConstraintReasonCode.POOR_SLEEP)
        applied_constraints.append("poor_sleep")

    # Priority 5: Low motivation
    if feedback.motivation_level is not None and feedback.motivation_level <= 3:
        if "pain_restriction" not in applied_constraints:
            constraints.intensity_cap = "moderate"
            constraints.volume_multiplier = min(constraints.volume_multiplier, 0.9)
        reason_codes.append(ConstraintReasonCode.LOW_MOTIVATION)
        applied_constraints.append("low_motivation")

    # Priority 6: Recovery mismatch (feedback conflicts with training metrics)
    if _detect_recovery_mismatch(feedback, recovery_state, training_summary):
        # If user reports fatigue but metrics show low load, trust user feedback
        if feedback.fatigue_level is not None and feedback.fatigue_level >= 6:
            constraints.volume_multiplier = min(constraints.volume_multiplier, 0.85)
            if not reason_codes:
                reason_codes.append(ConstraintReasonCode.RECOVERY_MISMATCH)
        applied_constraints.append("recovery_mismatch")

    # If no constraints applied, return neutral constraints
    if not applied_constraints:
        constraints.confidence = 0.0
        constraints.explanation = "No constraints derived from feedback."
        return constraints

    # Enforce bounds
    constraints = _enforce_bounds(constraints)

    # Limit reason codes to 1-2 max (keep most important)
    constraints.reason_codes = reason_codes[:2]

    # Calculate confidence
    constraints.confidence = _calculate_confidence(feedback, applied_constraints)

    # Adjust expiry based on confidence
    if constraints.confidence < 0.5:
        constraints.expiry_date = min(constraints.expiry_date, datetime.now(timezone.utc).date() + timedelta(days=3))

    # Generate explanation
    constraints.explanation = _generate_explanation(constraints, reason_codes)

    # Log decision
    logger.info(
        "B17: Constraints derived from feedback",
        feedback_fatigue=feedback.fatigue_level,
        feedback_soreness=feedback.soreness_level,
        feedback_pain=feedback.pain_reported,
        volume_multiplier=constraints.volume_multiplier,
        intensity_cap=constraints.intensity_cap,
        force_rest_days=constraints.force_rest_days,
        disallow_intensity=constraints.disallow_intensity_days,
        reason_codes=[code.value for code in constraints.reason_codes],
        confidence=constraints.confidence,
        expiry_date=constraints.expiry_date.isoformat(),
    )

    return constraints


def _calculate_expiry_date() -> date:
    """Calculate expiry date (max 7 days from today)."""
    return datetime.now(timezone.utc).date() + timedelta(days=7)


def _detect_recovery_mismatch(
    feedback: UserFeedback,
    recovery_state: RecoveryState,
    training_summary: TrainingSummary,
) -> bool:
    """Detect if user feedback conflicts with training metrics.

    Returns True if there's a mismatch (e.g., user reports fatigue but ATL is low).
    """
    # User reports high fatigue but ATL is low
    if feedback.fatigue_level is not None and feedback.fatigue_level >= 6:
        atl = recovery_state.atl
        if atl < 30.0:  # Low ATL threshold
            return True

    # User reports low motivation but compliance is high
    if feedback.motivation_level is not None and feedback.motivation_level <= 3:
        compliance = training_summary.execution.get("compliance_rate", 0.0)
        if isinstance(compliance, (int, float)) and compliance > 0.8:
            return True

    return False


def _enforce_bounds(constraints: TrainingConstraints) -> TrainingConstraints:
    """Enforce hard bounds on constraints.

    Args:
        constraints: Constraints to validate

    Returns:
        Constraints with bounds enforced
    """
    # Volume multiplier: 0.6-1.1
    if constraints.volume_multiplier < 0.6:
        logger.warning(f"Volume multiplier {constraints.volume_multiplier} below 0.6, clamping to 0.6")
        constraints.volume_multiplier = 0.6
    if constraints.volume_multiplier > 1.1:
        logger.warning(f"Volume multiplier {constraints.volume_multiplier} above 1.1, clamping to 1.1")
        constraints.volume_multiplier = 1.1

    # Force rest days: 0-3
    if constraints.force_rest_days > 3:
        logger.warning(f"Force rest days {constraints.force_rest_days} exceeds 3, clamping to 3")
        constraints.force_rest_days = 3

    return constraints


def _calculate_confidence(feedback: UserFeedback, applied_constraints: list[str]) -> float:
    """Calculate confidence score for constraint derivation.

    Args:
        feedback: User feedback
        applied_constraints: List of constraint names applied

    Returns:
        Confidence score (0.0-1.0)
    """
    if not applied_constraints:
        return 0.0

    # Base confidence from signal clarity
    base_confidence = 0.5

    # Multiple signals increase confidence
    signal_count = sum([
        feedback.fatigue_level is not None,
        feedback.soreness_level is not None,
        feedback.pain_reported,
        feedback.motivation_level is not None,
        feedback.sleep_quality_days is not None,
    ])
    if signal_count >= 3:
        base_confidence = 0.75
    elif signal_count == 2:
        base_confidence = 0.65

    # Pain reports have very high confidence
    if "pain_restriction" in applied_constraints:
        base_confidence = 0.95

    # High fatigue scores increase confidence
    if feedback.fatigue_level is not None and feedback.fatigue_level >= 8:
        base_confidence = min(1.0, base_confidence + 0.1)

    return min(1.0, base_confidence)


def _generate_explanation(
    constraints: TrainingConstraints,
    reason_codes: list[ConstraintReasonCode],
) -> str:
    """Generate a single factual sentence explaining constraints.

    Rules:
    - No coaching language
    - No recommendations
    - No future planning
    - Just facts

    Args:
        constraints: Derived constraints
        reason_codes: Reason codes

    Returns:
        Single factual sentence
    """
    if not reason_codes:
        return "No constraints derived from feedback."

    # Build explanation from reason codes
    reasons = []
    if ConstraintReasonCode.REPORTED_PAIN in reason_codes:
        reasons.append("reported pain")
    if ConstraintReasonCode.HIGH_FATIGUE in reason_codes:
        reasons.append("high reported fatigue")
    if ConstraintReasonCode.POOR_SLEEP in reason_codes:
        reasons.append("poor sleep")
    if ConstraintReasonCode.LOW_MOTIVATION in reason_codes:
        reasons.append("low motivation")
    if ConstraintReasonCode.SYSTEMIC_SORENESS in reason_codes:
        reasons.append("elevated soreness")
    if ConstraintReasonCode.RECOVERY_MISMATCH in reason_codes:
        reasons.append("recovery mismatch")

    # Add metric context if relevant
    metric_parts = []
    if constraints.volume_multiplier < 1.0:
        metric_parts.append(f"volume reduced by {int((1.0 - constraints.volume_multiplier) * 100)}%")
    if constraints.intensity_cap != "none":
        metric_parts.append(f"intensity capped at {constraints.intensity_cap}")
    if constraints.force_rest_days > 0:
        metric_parts.append(f"{constraints.force_rest_days} rest day(s) required")

    # Construct sentence
    reason_text = " and ".join(reasons)
    if metric_parts:
        metric_text = ", ".join(metric_parts)
        return f"Training constraints applied due to {reason_text} ({metric_text})."
    return f"Training constraints applied due to {reason_text}."


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================


def translate_feedback_to_constraints_entry(
    feedback: UserFeedback,
    training_summary: TrainingSummary | None = None,
    recovery_state: RecoveryState | None = None,
) -> TrainingConstraints:
    """Main entry point: translate feedback to constraints.

    This function handles the case where TrainingSummary or RecoveryState
    may not be available, using safe defaults.

    Args:
        feedback: User feedback signals
        training_summary: Optional training summary (defaults to minimal)
        recovery_state: Optional recovery state (defaults to neutral)

    Returns:
        TrainingConstraints with derived constraints
    """
    # Default training summary if not provided
    if training_summary is None:
        today = datetime.now(timezone.utc).date()
        training_summary = TrainingSummary(
            window_start=today,
            window_end=today,
            days=1,
            volume={},
            intensity_distribution={},
            load={"ctl": 0.0, "atl": 0.0, "tsb": 0.0, "trend": "stable"},
            execution={"compliance_rate": 1.0},
            anomalies=[],
            last_key_sessions=[],
            reliability_flags=ReliabilityFlags(
                low_compliance=False,
                high_variance=False,
                sparse_data=True,
            ),
        )

    # Default recovery state if not provided
    if recovery_state is None:
        recovery_state = RecoveryState(
            atl=0.0,
            tsb=0.0,
            recovery_status="adequate",
            risk_flags=[],
        )

    return translate_feedback_to_constraints(feedback, training_summary, recovery_state)
