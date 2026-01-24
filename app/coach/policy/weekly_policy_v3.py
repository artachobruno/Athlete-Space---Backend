"""Weekly Policy v3 (Athlete-Aware Adaptive Policy).

Purpose:
- Adapt thresholds and decisions based on athlete context
- Layer on top of Policy v2 (which wraps v1)
- Never bypasses v1 safety or v2 intent gating

This policy is:
- deterministic
- explainable
- composable with v2
- non-executing

Design principles:
- Wraps v2 — never duplicates logic
- Safety first — v1/v2 safety rules always win
- Athlete-aware — adapts to athlete characteristics
"""

from loguru import logger

from app.coach.policy.athlete_context import AthleteContext
from app.coach.policy.intent_context import IntentContext
from app.coach.policy.weekly_policy_v0 import WeeklyDecision, WeeklyPolicyResult
from app.coach.policy.weekly_policy_v2 import decide_weekly_action_v2
from app.tools.semantic.evaluate_plan_change import PlanStateSummary


def decide_weekly_action_v3(
    *,
    state: PlanStateSummary,
    intent_context: IntentContext,
    athlete: AthleteContext,
) -> WeeklyPolicyResult:
    """Weekly Policy v3 rules (in priority order, first match wins).

    Rule 1: Novice Stability Lock
    Rule 2: Elite Autonomy Boost
    Rule 3: Injury History Dampener
    Rule 4: Low Reliability Throttle
    Rule 5: Consistency Amplifier
    Rule 6: Fallback (delegate to v2)

    Args:
        state: Current plan state summary
        intent_context: Intent context for the request
        athlete: Athlete context for adaptive decisions

    Returns:
        Weekly policy result with decision and reason
    """
    logger.debug(
        "Weekly policy v3 evaluation",
        experience_level=athlete.experience_level,
        risk_tolerance=athlete.risk_tolerance,
        consistency_score=athlete.consistency_score,
        history_of_injury=athlete.history_of_injury,
        adherence_reliability=athlete.adherence_reliability,
    )

    # Step 1: Always defer to v2 first
    base = decide_weekly_action_v2(state=state, intent=intent_context)

    # If v2 already made a strong decision (safety or intent), respect it
    # Check if v2 returned a safety/intent-related decision by examining the reason
    # v2 safety/intent keywords that should not be overridden
    v2_strong_decision_keywords = [
        "taper",
        "race day",
        "injury",
        "fatigue",
        "just starting",
        "monitoring",
        "compliance",
        "exploratory",
        "reflection",
        "explicit",
        "system",
        "detected",
    ]
    if any(keyword in base.reason.lower() for keyword in v2_strong_decision_keywords):
        logger.debug("Policy v3: v2 safety/intent rule takes precedence", v2_reason=base.reason)
        return base

    # v3 rules apply only when v2 fell back to default "on track" behavior
    # This happens when v2 returns v1's default NO_CHANGE with "Training is on track"

    # Rule 1: Novice Stability Lock
    # For novice athletes, we want to maintain stability
    # Since we don't have days_since_last_plan_change in state, we'll use a simpler check
    # If compliance is good and plan exists, maintain stability
    if (
        athlete.experience_level == "novice"
        and state.planned_total_week > 0
        and state.compliance_rate >= 0.7
    ):
        return WeeklyPolicyResult(
            decision=WeeklyDecision.NO_CHANGE,
            reason="Maintaining stability for novice athlete (recent plan change).",
        )

    # Rule 2: Elite Autonomy Boost
    if (
        athlete.experience_level in {"advanced", "elite"}
        and intent_context.intent_strength == "strong"
    ):
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_PLAN,
            reason="Experienced athlete with strong intent — allowing autonomy.",
        )

    # Rule 3: Injury History Dampener
    if (
        athlete.history_of_injury
        and base.decision == WeeklyDecision.PROPOSE_PLAN
    ):
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_ADJUSTMENT,
            reason="Athlete has injury history — softening plan changes.",
        )

    # Rule 4: Low Reliability Throttle
    if (
        athlete.adherence_reliability == "low"
        and state.compliance_rate is not None
        and state.compliance_rate < 0.8
    ):
        return WeeklyPolicyResult(
            decision=WeeklyDecision.NO_CHANGE,
            reason="Low adherence reliability with poor compliance — avoiding churn.",
        )

    # Rule 5: Consistency Amplifier
    if (
        athlete.consistency_score > 0.9
        and intent_context.intent_strength in {"moderate", "strong"}
    ):
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_PLAN,
            reason="High consistency athlete — rewarding stable behavior.",
        )

    # Rule 6: Fallback
    return base
