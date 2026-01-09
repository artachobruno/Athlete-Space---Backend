"""B18: Training Load Adjustment Tool.

Safely applies training load changes using bounded, auditable, deterministic rules.
Consumes TrainingConstraints (B17) and canonical training data.

This tool does NOT plan workouts.
It adjusts load parameters that planning (B8) will respect.

DESIGN PRINCIPLES:
- Deterministic (same inputs → same outputs)
- Constraint-driven (B17 is the authority)
- Bounded (hard limits, no stacking)
- Explainable (explicit deltas + reason codes)
- Auditable (logs + progress events)
- No LLM usage
- No direct calendar mutation
- One action per turn (executor enforced)
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger

from app.calendar.training_summary import TrainingSummary
from app.coach.schemas.constraints import ConstraintReasonCode, TrainingConstraints
from app.coach.schemas.load_adjustment import AdjustmentReasonCode, LoadAdjustmentDecision
from app.coach.utils.constraints import RecoveryState


def adjust_training_load(
    training_summary: TrainingSummary,
    recovery_state: RecoveryState,
    constraints: TrainingConstraints | None = None,
) -> LoadAdjustmentDecision:
    """Adjust training load based on constraints and training state.

    Args:
        training_summary: Training summary (B16) with load metrics and execution data
        recovery_state: Recovery state (B19) with ATL, TSB, recovery_status
        constraints: Optional training constraints (B17) - if None, uses defaults

    Returns:
        LoadAdjustmentDecision with volume/intensity adjustments

    Rules:
        - Deterministic: same inputs → same outputs
        - Bounded: all adjustments within hard limits
        - Constraint-driven: constraints are the authority
        - No stacking: pick most restrictive rule
    """
    if constraints is None:
        constraints = TrainingConstraints(
            volume_multiplier=1.0,
            intensity_cap="none",
            force_rest_days=0,
            disallow_intensity_days=set(),
            long_session_cap_minutes=None,
            expiry_date=datetime.now(timezone.utc).date() + timedelta(days=7),
            source="user_feedback",
            confidence=0.0,
            reason_codes=[],
            explanation="",
            created_at=datetime.now(timezone.utc),
        )

    logger.info(
        "B18: Starting load adjustment",
        volume_multiplier=constraints.volume_multiplier,
        intensity_cap=constraints.intensity_cap,
        force_rest_days=constraints.force_rest_days,
        atl=recovery_state.atl,
        tsb=recovery_state.tsb,
    )

    # TODO-B18.1: Establish base adjustment window
    effective_window_days = _compute_adjustment_window(constraints)

    # TODO-B18.2: Compute volume adjustment
    volume_delta_pct = _compute_volume_adjustment(constraints, training_summary)

    # TODO-B18.3: Compute intensity adjustment
    intensity_cap = _compute_intensity_adjustment(constraints, recovery_state, training_summary)

    # TODO-B18.4: Long session & rest enforcement
    long_session_cap_minutes = constraints.long_session_cap_minutes
    forced_rest_days = _compute_forced_rest_days(constraints, training_summary, effective_window_days)

    # Collect reason codes
    reason_codes = _collect_reason_codes(constraints, recovery_state, training_summary)

    # Compute confidence
    confidence = _compute_confidence(constraints, recovery_state, training_summary)

    # TODO-B18.5: Enforce hard bounds (before returning)
    volume_delta_pct = max(-0.40, min(0.10, volume_delta_pct))
    if intensity_cap not in {"easy", "moderate", "none"}:
        intensity_cap = "none"
    if long_session_cap_minutes is not None and long_session_cap_minutes < 45:
        long_session_cap_minutes = 45
    if len(forced_rest_days) > 2:
        forced_rest_days = forced_rest_days[:2]

    # TODO-B18.6: Generate explanation (non-LLM)
    explanation = _generate_explanation(volume_delta_pct, intensity_cap, reason_codes, forced_rest_days)

    # Collect applied constraints
    applied_constraints: list[str] = []
    if constraints.volume_multiplier != 1.0:
        applied_constraints.append("volume_multiplier")
    if constraints.intensity_cap != "none":
        applied_constraints.append("intensity_cap")
    if constraints.force_rest_days > 0:
        applied_constraints.append("force_rest_days")
    if constraints.long_session_cap_minutes is not None:
        applied_constraints.append("long_session_cap_minutes")
    if constraints.disallow_intensity_days:
        applied_constraints.append("disallow_intensity_days")

    decision = LoadAdjustmentDecision(
        volume_delta_pct=volume_delta_pct,
        intensity_cap=intensity_cap,
        long_session_cap_minutes=long_session_cap_minutes,
        forced_rest_days=forced_rest_days,
        effective_window_days=effective_window_days,
        reason_codes=reason_codes,
        confidence=confidence,
        explanation=explanation,
        applied_constraints=applied_constraints,
    )

    # TODO-B18.7: Decision logging
    _log_decision(decision, constraints, training_summary)

    return decision


def _compute_adjustment_window(constraints: TrainingConstraints) -> int:
    """Compute effective adjustment window in days.

    Rules:
    - Default window: 7 days
    - If constraints.expiry_date < now + 7: shrink window
    - If confidence < 0.5 → window ≤ 3 days

    Args:
        constraints: Training constraints

    Returns:
        Effective window in days (1-7)
    """
    today = datetime.now(timezone.utc).date()
    days_until_expiry = (constraints.expiry_date - today).days

    # Default window
    window = 7

    # Shrink if expiry is sooner
    if days_until_expiry < window:
        window = max(1, days_until_expiry)

    # Shrink if low confidence
    if constraints.confidence < 0.5:
        window = min(window, 3)

    return window


def _compute_volume_adjustment(
    constraints: TrainingConstraints,
    training_summary: TrainingSummary,
) -> float:
    """Compute volume adjustment percentage.

    Rules (pick most restrictive):
    - TrainingConstraints.volume_multiplier → convert to delta
    - ATL/CTL ratio > 1.5 → cap reduction at ≥ 20%
    - TSB < -25 → force ≥ 15% reduction
    - High variance week → no increase allowed

    Args:
        constraints: Training constraints
        recovery_state: Recovery state
        training_summary: Training summary

    Returns:
        Volume delta percentage (-0.40 to +0.10)
    """
    # Start with constraint multiplier
    volume_multiplier = constraints.volume_multiplier
    volume_delta_pct = volume_multiplier - 1.0

    # Get load metrics (ensure float types)
    load_metrics = training_summary.load
    ctl_raw = load_metrics.get("ctl", 0.0)
    atl_raw = load_metrics.get("atl", 0.0)
    tsb_raw = load_metrics.get("tsb", 0.0)

    # Convert to float (handle case where dict values might be str)
    ctl = float(ctl_raw) if isinstance(ctl_raw, (int, float)) else 0.0
    atl = float(atl_raw) if isinstance(atl_raw, (int, float)) else 0.0
    tsb = float(tsb_raw) if isinstance(tsb_raw, (int, float)) else 0.0

    # Rule: ATL/CTL ratio > 1.5 → cap reduction at ≥ 20%
    if ctl > 0:
        atl_ctl_ratio = atl / ctl
        if atl_ctl_ratio > 1.5:
            volume_delta_pct = min(volume_delta_pct, -0.20)
            logger.debug(f"ATL/CTL ratio {atl_ctl_ratio:.2f} > 1.5, capping reduction at -20%")

    # Rule: TSB < -25 → force ≥ 15% reduction
    if tsb < -25.0:
        volume_delta_pct = min(volume_delta_pct, -0.15)
        logger.debug(f"TSB {tsb:.1f} < -25, forcing ≥15% reduction")

    # Rule: High variance week → no increase allowed
    if training_summary.reliability_flags.high_variance:
        volume_delta_pct = min(volume_delta_pct, 0.0)
        logger.debug("High variance week detected, no increase allowed")

    # Clamp to bounds
    return max(-0.40, min(0.10, volume_delta_pct))


def _compute_intensity_adjustment(
    constraints: TrainingConstraints,
    recovery_state: RecoveryState,
    training_summary: TrainingSummary,
) -> Literal["easy", "moderate", "none"]:
    """Compute intensity adjustment.

    Rules:
    - If constraints.intensity_cap present → enforce
    - If recovery_state = poor → cap at moderate
    - If back-to-back hard days detected → disallow hard days

    Args:
        constraints: Training constraints
        recovery_state: Recovery state
        training_summary: Training summary

    Returns:
        Intensity cap: "easy", "moderate", or "none"
    """
    # Start with constraint cap (convert "hard" to "none" for our return type)
    constraint_cap = constraints.intensity_cap
    if constraint_cap == "hard":
        intensity_cap: Literal["easy", "moderate", "none"] = "none"
    elif constraint_cap == "easy":
        intensity_cap = "easy"
    elif constraint_cap == "moderate":
        intensity_cap = "moderate"
    else:
        intensity_cap = "none"

    # Rule: If recovery_state = poor → cap at moderate
    if recovery_state.recovery_status == "over" and intensity_cap in {"none", "hard"}:
        intensity_cap = "moderate"

    # Rule: If back-to-back hard days detected → disallow hard days
    if "High intensity clustered on back-to-back days" in training_summary.anomalies and intensity_cap in {"none", "hard"}:
        intensity_cap = "moderate"

    # Rule: If constraints disallow hard days → cap at moderate
    if "hard" in constraints.disallow_intensity_days and intensity_cap in {"none", "hard"}:
        intensity_cap = "moderate"

    return intensity_cap


def _compute_forced_rest_days(
    constraints: TrainingConstraints,
    training_summary: TrainingSummary,
    effective_window_days: int,
) -> list[str]:
    """Compute forced rest days.

    Rules:
    - Respect force_rest_days count from constraints
    - Choose next available low-importance days
    - Never override race day or explicitly planned key sessions
    - Output dates only, no mutation

    Args:
        constraints: Training constraints
        training_summary: Training summary
        effective_window_days: Effective window in days

    Returns:
        List of ISO date strings (YYYY-MM-DD) for forced rest days
    """
    if constraints.force_rest_days == 0:
        return []

    today = datetime.now(timezone.utc).date()
    forced_rest_days: list[str] = []

    # Get key session dates to avoid
    key_session_dates: set[date] = set()
    for key_session in training_summary.last_key_sessions:
        try:
            session_date = datetime.fromisoformat(key_session.date).date()
            key_session_dates.add(session_date)
        except (ValueError, AttributeError):
            pass

    # Choose days within effective window, avoiding key sessions
    days_to_choose = constraints.force_rest_days
    candidate_date = today + timedelta(days=1)  # Start from tomorrow

    while len(forced_rest_days) < days_to_choose:
        # Check if we're within window
        days_ahead = (candidate_date - today).days
        if days_ahead > effective_window_days:
            break

        # Skip if it's a key session date
        if candidate_date not in key_session_dates:
            forced_rest_days.append(candidate_date.isoformat())

        candidate_date += timedelta(days=1)

        # Safety: don't go too far ahead
        if days_ahead > 14:
            break

    return forced_rest_days


def _collect_reason_codes(
    constraints: TrainingConstraints,
    recovery_state: RecoveryState,
    training_summary: TrainingSummary,
) -> list[AdjustmentReasonCode]:
    """Collect reason codes for the adjustment.

    Args:
        constraints: Training constraints
        recovery_state: Recovery state
        training_summary: Training summary

    Returns:
        List of reason codes (1-3 max)
    """
    reason_codes: list[AdjustmentReasonCode] = []

    # Map constraint reason codes to adjustment reason codes
    if ConstraintReasonCode.HIGH_FATIGUE in constraints.reason_codes:
        reason_codes.append(AdjustmentReasonCode.HIGH_FATIGUE)

    # Check ATL spike (ensure float types)
    load_metrics = training_summary.load
    atl_raw = load_metrics.get("atl", 0.0)
    ctl_raw = load_metrics.get("ctl", 0.0)
    tsb_raw = load_metrics.get("tsb", 0.0)

    # Convert to float (handle case where dict values might be str)
    atl = float(atl_raw) if isinstance(atl_raw, (int, float)) else 0.0
    ctl = float(ctl_raw) if isinstance(ctl_raw, (int, float)) else 0.0
    tsb = float(tsb_raw) if isinstance(tsb_raw, (int, float)) else 0.0

    if ctl > 0 and atl / ctl > 1.5:
        reason_codes.append(AdjustmentReasonCode.ATL_SPIKE)

    # Check TSB
    if tsb < -25.0:
        reason_codes.append(AdjustmentReasonCode.TSB_LOW)

    # Check high variance
    if training_summary.reliability_flags.high_variance:
        reason_codes.append(AdjustmentReasonCode.HIGH_VARIANCE)

    # Check recovery status
    if recovery_state.recovery_status == "over":
        reason_codes.append(AdjustmentReasonCode.POOR_RECOVERY)

    # Check back-to-back hard days
    if "High intensity clustered on back-to-back days" in training_summary.anomalies:
        reason_codes.append(AdjustmentReasonCode.BACK_TO_BACK_HARD)

    # If constraints were applied, add constraint-driven reason
    if constraints.volume_multiplier != 1.0 or constraints.intensity_cap != "none":
        reason_codes.append(AdjustmentReasonCode.CONSTRAINT_DRIVEN)

    # Limit to 3 max, keep most important
    return reason_codes[:3]


def _compute_confidence(
    constraints: TrainingConstraints,
    recovery_state: RecoveryState,
    training_summary: TrainingSummary,
) -> float:
    """Compute confidence score for the adjustment.

    Args:
        constraints: Training constraints
        recovery_state: Recovery state
        training_summary: Training summary

    Returns:
        Confidence score (0.0-1.0)
    """
    # Base confidence from constraint confidence
    base_confidence = constraints.confidence

    # Increase if multiple signals align
    signal_count = 0
    if constraints.volume_multiplier != 1.0:
        signal_count += 1
    if constraints.intensity_cap != "none":
        signal_count += 1
    if recovery_state.recovery_status == "over":
        signal_count += 1
    if training_summary.reliability_flags.high_variance:
        signal_count += 1

    if signal_count >= 3:
        base_confidence = max(base_confidence, 0.8)
    elif signal_count == 2:
        base_confidence = max(base_confidence, 0.6)

    # Decrease if conflicting signals
    if constraints.volume_multiplier > 1.0 and recovery_state.recovery_status == "over":
        base_confidence = min(base_confidence, 0.5)

    return min(1.0, max(0.0, base_confidence))


def _generate_explanation(
    volume_delta_pct: float,
    intensity_cap: Literal["easy", "moderate", "none"],
    reason_codes: list[AdjustmentReasonCode],
    forced_rest_days: list[str],
) -> str:
    """Generate explanation (non-LLM).

    Rules:
    - One factual sentence
    - Reference only reason codes + deltas
    - No advice language

    Args:
        volume_delta_pct: Volume delta percentage
        intensity_cap: Intensity cap
        reason_codes: Reason codes
        forced_rest_days: Forced rest days

    Returns:
        Explanation string
    """
    if not reason_codes:
        return "No training load adjustments applied."

    # Build explanation parts
    parts: list[str] = []

    # Volume adjustment
    if abs(volume_delta_pct) > 0.01:
        if volume_delta_pct < 0:
            pct = int(abs(volume_delta_pct) * 100)
            parts.append(f"training volume reduced by {pct}%")
        else:
            pct = int(volume_delta_pct * 100)
            parts.append(f"training volume increased by {pct}%")

    # Intensity cap
    if intensity_cap != "none":
        parts.append(f"intensity capped at {intensity_cap}")

    # Rest days
    if forced_rest_days:
        parts.append(f"{len(forced_rest_days)} rest day(s) required")

    # Reason codes
    reason_texts: list[str] = []
    if AdjustmentReasonCode.HIGH_FATIGUE in reason_codes:
        reason_texts.append("high reported fatigue")
    if AdjustmentReasonCode.ATL_SPIKE in reason_codes:
        reason_texts.append("elevated acute load")
    if AdjustmentReasonCode.TSB_LOW in reason_codes:
        reason_texts.append("low training stress balance")
    if AdjustmentReasonCode.POOR_RECOVERY in reason_codes:
        reason_texts.append("poor recovery status")
    if AdjustmentReasonCode.HIGH_VARIANCE in reason_codes:
        reason_texts.append("high training variance")
    if AdjustmentReasonCode.BACK_TO_BACK_HARD in reason_codes:
        reason_texts.append("back-to-back hard days detected")

    # Construct sentence
    if parts:
        adjustments = ", ".join(parts)
        if reason_texts:
            reasons = " and ".join(reason_texts)
            return f"Training load adjusted ({adjustments}) due to {reasons}."
        return f"Training load adjusted ({adjustments})."
    if reason_texts:
        reasons = " and ".join(reason_texts)
        return f"Training load adjustment considered due to {reasons}."

    return "Training load adjustment applied."


def _log_decision(
    decision: LoadAdjustmentDecision,
    constraints: TrainingConstraints,
    training_summary: TrainingSummary,
) -> None:
    """Log decision for auditability.

    Logs:
    - Inputs (summary hash, constraint hash)
    - Output deltas
    - Reason codes
    - Confidence
    - Effective window

    Args:
        decision: Load adjustment decision
        constraints: Training constraints
        training_summary: Training summary
        recovery_state: Recovery state
    """
    # Create simple hashes for inputs (using key fields)
    summary_hash = hash((
        training_summary.window_start.isoformat(),
        training_summary.window_end.isoformat(),
        training_summary.load.get("ctl", 0.0),
        training_summary.load.get("atl", 0.0),
    ))
    constraint_hash = hash((
        constraints.volume_multiplier,
        constraints.intensity_cap,
        constraints.force_rest_days,
        constraints.expiry_date.isoformat(),
    ))

    logger.info(
        "B18: Load adjustment decision",
        summary_hash=abs(summary_hash) % 10000,  # Simple hash for logging
        constraint_hash=abs(constraint_hash) % 10000,
        volume_delta_pct=decision.volume_delta_pct,
        intensity_cap=decision.intensity_cap,
        long_session_cap_minutes=decision.long_session_cap_minutes,
        forced_rest_days_count=len(decision.forced_rest_days),
        forced_rest_days=decision.forced_rest_days,
        effective_window_days=decision.effective_window_days,
        reason_codes=[code.value for code in decision.reason_codes],
        confidence=decision.confidence,
        applied_constraints=decision.applied_constraints,
        explanation=decision.explanation,
    )
