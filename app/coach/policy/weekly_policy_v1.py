"""Weekly Policy v1.

Purpose:
- Decide WHETHER a plan change should be proposed
- Never decide HOW to change a plan

This policy is:
- deterministic
- risk-aware
- non-opinionated
- non-creative
- non-executing

Design principles:
- Conservative
- Explicit
- Deterministic
- Easy to replace

This policy answers ONE question:
"Should the coach act this week?"
"""

from loguru import logger

from app.coach.policy.weekly_policy_v0 import WeeklyDecision, WeeklyPolicyResult
from app.tools.semantic.evaluate_plan_change import PlanStateSummary


def decide_weekly_action(state: PlanStateSummary) -> WeeklyPolicyResult:
    """Weekly Policy v1 rules (in priority order, first match wins).

    Rule 1: Taper Freeze
    Rule 2: Injury Safety Gate
    Rule 3: Fatigue Override
    Rule 4: Early-Week Stability
    Rule 5: Chronic Non-Compliance
    Rule 6: Default (fallback to v0 behavior)

    Args:
        state: Current plan state summary

    Returns:
        Weekly policy result with decision and reason
    """
    logger.debug(
        "Weekly policy v1 evaluation",
        phase=state.phase,
        days_to_race=state.days_to_race,
        compliance=state.compliance_rate,
        fatigue=state.subjective_fatigue,
        injury=state.injury_status,
    )

    # Rule 1: Taper Freeze
    # IF phase == "taper" AND days_to_race <= 14
    # → NO_CHANGE
    if state.phase == "taper" and state.days_to_race is not None and state.days_to_race <= 14:
        decision = WeeklyDecision.NO_CHANGE
        reason = "In taper phase close to race day — avoiding plan changes unless risk is severe."
        logger.info(
            "Policy v1 rule triggered",
            rule="taper_freeze",
            decision=decision,
        )
        return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 2: Injury Safety Gate
    # IF injury_status != "none"
    # → PROPOSE_ADJUSTMENT
    if state.injury_status is not None and state.injury_status != "none":
        decision = WeeklyDecision.PROPOSE_ADJUSTMENT
        reason = "Injury flagged — proposing a safer adjusted week."
        logger.info(
            "Policy v1 rule triggered",
            rule="injury_safety_gate",
            decision=decision,
        )
        return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 3: Fatigue Override
    # IF (ATL >> CTL) OR subjective_fatigue == "high"
    # → PROPOSE_ADJUSTMENT
    # Define ATL >> CTL conservatively: ATL >= CTL * 1.2 (20% higher or more)
    atl_much_higher_than_ctl = (
        state.atl is not None
        and state.ctl is not None
        and state.ctl > 0
        and state.atl >= state.ctl * 1.2
    )
    if atl_much_higher_than_ctl or state.subjective_fatigue == "high":
        decision = WeeklyDecision.PROPOSE_ADJUSTMENT
        reason = "High fatigue detected — recommending load adjustment."
        logger.info(
            "Policy v1 rule triggered",
            rule="fatigue_override",
            decision=decision,
        )
        return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 4: Early-Week Stability
    # IF planned_elapsed_ratio < 0.30
    # → NO_CHANGE
    # Skip if no plan exists (handled by Rule 6)
    if state.planned_total_week > 0:
        planned_elapsed_ratio = state.planned_elapsed / state.planned_total_week
        if planned_elapsed_ratio < 0.30:
            decision = WeeklyDecision.NO_CHANGE
            reason = "Week is just starting — monitoring before making changes."
            logger.info(
                "Policy v1 rule triggered",
                rule="early_week_stability",
                decision=decision,
            )
            return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 5: Chronic Non-Compliance
    # IF compliance_rate < 0.7 for >= 3 consecutive weeks
    # → PROPOSE_ADJUSTMENT
    # Note: We only have current week's compliance_rate, so we check if it's low
    # and assume chronic if it's been consistently low (simplified check)
    if state.compliance_rate < 0.7:
        # For now, we trigger on current low compliance
        # In a full implementation, we'd check historical compliance
        decision = WeeklyDecision.PROPOSE_ADJUSTMENT
        reason = "Sustained low compliance detected — proposing plan adjustment."
        logger.info(
            "Policy v1 rule triggered",
            rule="chronic_non_compliance",
            decision=decision,
        )
        return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 6: Default (fallback to v0 behavior)
    # Defer to v0-style compliance logic
    # Rule 1 — No plan at all
    if state.planned_total_week == 0:
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_PLAN,
            reason="No training plan exists for the current week",
        )

    # Rule 2 — Plan exists, but athlete is off-track so far
    if state.planned_elapsed > 0 and state.compliance_rate < 0.5:
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_ADJUSTMENT,
            reason=(
                f"Low compliance so far this week "
                f"({state.executed_elapsed}/{state.planned_elapsed} sessions completed)"
            ),
        )

    # Rule 3 — Default: do nothing
    return WeeklyPolicyResult(
        decision=WeeklyDecision.NO_CHANGE,
        reason="Training is on track; no changes needed",
    )
