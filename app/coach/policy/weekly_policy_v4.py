"""Weekly Policy v4 (Trajectory-Aware Strategic Policy).

Purpose:
- Add trajectory-aware strategic policy layer
- Wraps Policy v3 (which wraps v2, which wraps v1)
- Never bypasses v1 safety, v2 intent gating, or v3 athlete adaptation

This policy is:
- deterministic
- explainable
- composable with v3
- non-executing

Design principles:
- Wraps v3 — never duplicates logic
- Safety first — v1/v2/v3 safety rules always win
- Trajectory-aware — considers training trajectory patterns
"""

from enum import StrEnum

from loguru import logger

from app.coach.policy.weekly_policy_v0 import WeeklyDecision, WeeklyPolicyResult
from app.tools.semantic.evaluate_plan_change import PlanStateSummary


class TrajectoryState(StrEnum):
    """Training trajectory state derived from current plan state."""

    IMPROVING = "improving"
    STABLE = "stable"
    STAGNANT = "stagnant"
    DECLINING = "declining"
    VOLATILE = "volatile"


def derive_trajectory(state: PlanStateSummary) -> TrajectoryState:
    """Derive training trajectory from plan state summary.

    Uses only data available in PlanStateSummary (no history DB reads).

    Args:
        state: Current plan state summary

    Returns:
        Trajectory state based on compliance and load metrics
    """
    compliance = state.compliance_rate
    atl = state.atl
    ctl = state.ctl

    # Rule 1: VOLATILE (check first to catch edge cases)
    # High variance indicators (simplified check)
    # If compliance is very low (< 0.5) or very high (> 0.9) with inconsistent load
    if compliance < 0.5:
        return TrajectoryState.VOLATILE
    if (
        compliance > 0.9
        and atl is not None
        and ctl is not None
        and ctl > 0
        and abs(atl - ctl) > ctl * 0.3
    ):
        return TrajectoryState.VOLATILE

    # Rule 2: IMPROVING
    # compliance ≥ 0.85 AND atl ≤ ctl * 1.1
    if (
        compliance >= 0.85
        and atl is not None
        and ctl is not None
        and ctl > 0
        and atl <= ctl * 1.1
    ):
        return TrajectoryState.IMPROVING

    # Rule 3: STABLE
    # compliance ≥ 0.75 AND atl ≤ ctl * 1.2
    if (
        compliance >= 0.75
        and atl is not None
        and ctl is not None
        and ctl > 0
        and atl <= ctl * 1.2
    ):
        return TrajectoryState.STABLE

    # Rule 4: DECLINING
    # compliance < 0.7 AND atl > ctl * 1.2
    if (
        compliance < 0.7
        and atl is not None
        and ctl is not None
        and ctl > 0
        and atl > ctl * 1.2
    ):
        return TrajectoryState.DECLINING

    # Rule 5: STAGNANT
    # compliance < 0.7 AND atl ≈ ctl (within 10%)
    if compliance < 0.7 and atl is not None and ctl is not None and ctl > 0:
        atl_ctl_ratio = atl / ctl
        if 0.9 <= atl_ctl_ratio <= 1.1:  # atl ≈ ctl
            return TrajectoryState.STAGNANT

    # Default: STABLE if we have good compliance, otherwise STAGNANT
    if compliance >= 0.7:
        return TrajectoryState.STABLE
    return TrajectoryState.STAGNANT


def decide_weekly_action_v4(
    state: PlanStateSummary,
    *,
    prior_decision: WeeklyPolicyResult,
) -> WeeklyPolicyResult:
    """Weekly Policy v4 rules (in priority order, first match wins).

    Rule v4.1: Trajectory Lock
    Rule v4.2: Churn Guard
    Rule v4.3: Decline Recovery
    Rule v4.4: Volatility Dampener
    Rule v4.5: Late-Stage Freeze
    Rule v4.6: Strategic Green Light
    Rule v4.7: Fallback (delegate to v3)

    Args:
        state: Current plan state summary
        prior_decision: Decision from Policy v3

    Returns:
        Weekly policy result with decision and reason
    """
    trajectory = derive_trajectory(state)

    logger.debug(
        "Weekly policy v4 evaluation",
        trajectory=trajectory.value,
        compliance=state.compliance_rate,
        atl=state.atl,
        ctl=state.ctl,
        days_to_race=state.days_to_race,
    )

    # Rule v4.1: Trajectory Lock
    # If trajectory is IMPROVING or STABLE and far from race, maintain momentum
    if (
        trajectory in {TrajectoryState.IMPROVING, TrajectoryState.STABLE}
        and state.days_to_race is not None
        and state.days_to_race > 21
    ):
        return WeeklyPolicyResult(
            decision=WeeklyDecision.NO_CHANGE,
            reason="v4.trajectory_lock: Current training is progressing well; change would reduce momentum.",
        )

    # Rule v4.2: Churn Guard
    # If too many plan changes recently, avoid more churn
    plan_changes = getattr(state, "plan_changes_last_21_days", None)
    if plan_changes is not None and plan_changes >= 2:
        return WeeklyPolicyResult(
            decision=WeeklyDecision.NO_CHANGE,
            reason="v4.churn_guard: Too many plan changes recently; maintaining stability.",
        )

    # Rule v4.3: Decline Recovery
    # If trajectory is DECLINING and we have time before race, propose adjustment
    if (
        trajectory == TrajectoryState.DECLINING
        and state.days_to_race is not None
        and state.days_to_race >= 28
    ):
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_ADJUSTMENT,
            reason="v4.decline_recovery: Training trajectory declining; intervention needed.",
        )

    # Rule v4.4: Volatility Dampener
    # If trajectory is VOLATILE, propose adjustment to stabilize
    if trajectory == TrajectoryState.VOLATILE:
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_ADJUSTMENT,
            reason="v4.volatility_dampener: Training pattern is volatile; proposing stabilization.",
        )

    # Rule v4.5: Late-Stage Freeze
    # If close to race and not declining, freeze changes
    if (
        state.days_to_race is not None
        and state.days_to_race <= 14
        and trajectory != TrajectoryState.DECLINING
    ):
        return WeeklyPolicyResult(
            decision=WeeklyDecision.NO_CHANGE,
            reason="v4.late_stage_freeze: Close to race day; maintaining current plan unless declining.",
        )

    # Rule v4.6: Strategic Green Light
    # If stagnant with strong intent and experienced athlete, allow change
    if trajectory == TrajectoryState.STAGNANT:
        user_intent_strength = getattr(state, "user_intent_strength", None)
        experience_level = getattr(state, "experience_level", None)
        if (
            user_intent_strength == "strong"
            and experience_level in {"intermediate", "advanced"}
        ):
            return WeeklyPolicyResult(
                decision=WeeklyDecision.PROPOSE_PLAN,
                reason="v4.strategic_green_light: Stagnant trajectory with strong intent; allowing strategic change.",
            )

    # Rule v4.7: Fallback (delegate to v3)
    logger.debug("Policy v4: falling back to v3 decision")
    return prior_decision
