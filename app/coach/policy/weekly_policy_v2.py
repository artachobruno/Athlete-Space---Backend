"""Weekly Policy v2 (Intent-Aware Gating).

Purpose:
- Gate plan changes using intent strength + source
- Layer on top of Policy v1 safety rules
- Never override safety, only add intent-based gating

This policy is:
- deterministic
- explainable
- composable with v1
- non-executing

Design principles:
- Wraps v1 — never duplicates logic
- Safety first — v1 safety rules always win
- Intent-aware — considers request source and strength
"""

from loguru import logger

from app.coach.policy.intent_context import IntentContext
from app.coach.policy.weekly_policy_v0 import WeeklyDecision, WeeklyPolicyResult
from app.coach.policy.weekly_policy_v1 import decide_weekly_action
from app.tools.semantic.evaluate_plan_change import PlanStateSummary


def decide_weekly_action_v2(
    *,
    state: PlanStateSummary,
    intent: IntentContext,
) -> WeeklyPolicyResult:
    """Weekly Policy v2 rules (in priority order, first match wins).

    Rule 1: Hard safety (delegate to v1)
    Rule 2: Weak intent suppression
    Rule 3: Reflective athlete intent
    Rule 4: Strong explicit athlete intent
    Rule 5: System detected issues
    Rule 6: Fallback (delegate to v1)

    Args:
        state: Current plan state summary
        intent: Intent context for the request

    Returns:
        Weekly policy result with decision and reason
    """
    logger.debug(
        "Weekly policy v2 evaluation",
        request_source=intent.request_source,
        intent_strength=intent.intent_strength,
        execution_requested=intent.execution_requested,
    )

    # Rule 1: Hard safety (delegate to v1)
    # v2 never overrides safety rules from v1
    v1_result = decide_weekly_action(state)

    # Check if v1 returned a safety-related decision
    # Safety decisions are: NO_CHANGE (taper/early week), PROPOSE_ADJUSTMENT (injury/fatigue)
    # If v1 returned PROPOSE_PLAN (no plan exists), we still allow v2 to process
    # If v1 returned safety-related decisions, we respect them
    if v1_result.decision == WeeklyDecision.NO_CHANGE:
        # Check if this is a safety-related NO_CHANGE (taper, early week)
        # vs a default NO_CHANGE (on track)
        safety_keywords = ["taper", "race day", "just starting", "monitoring"]
        if any(keyword in v1_result.reason.lower() for keyword in safety_keywords):
            logger.info(
                "Policy v2: v1 safety rule takes precedence",
                v1_decision=v1_result.decision,
                v1_reason=v1_result.reason,
            )
            return v1_result

    if v1_result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT:
        # Check if this is a safety-related PROPOSE_ADJUSTMENT (injury, fatigue, compliance)
        safety_keywords = ["injury", "fatigue", "compliance"]
        if any(keyword in v1_result.reason.lower() for keyword in safety_keywords):
            logger.info(
                "Policy v2: v1 safety rule takes precedence",
                v1_decision=v1_result.decision,
                v1_reason=v1_result.reason,
            )
            return v1_result

    # Rule 2: Weak intent suppression
    if intent.intent_strength == "weak":
        decision = WeeklyDecision.NO_CHANGE
        reason = "Exploratory request does not justify plan changes."
        logger.info(
            "Policy v2 rule triggered",
            rule="weak_intent_suppression",
            decision=decision,
        )
        return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 3: Reflective athlete intent
    if intent.request_source == "athlete_reflective":
        decision = WeeklyDecision.PROPOSE_ADJUSTMENT
        reason = "Athlete reflection detected — proposing adjustment for review."
        logger.info(
            "Policy v2 rule triggered",
            rule="reflective_athlete_intent",
            decision=decision,
        )
        return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 4: Strong explicit athlete intent
    if (
        intent.request_source == "athlete_explicit"
        and intent.intent_strength == "strong"
    ):
        decision = WeeklyDecision.PROPOSE_PLAN
        reason = "Explicit athlete request — proposing plan change."
        logger.info(
            "Policy v2 rule triggered",
            rule="strong_explicit_intent",
            decision=decision,
        )
        return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 5: System detected issues
    if intent.request_source == "system_detected":
        decision = WeeklyDecision.PROPOSE_ADJUSTMENT
        reason = "System-detected issue — recommending adjustment."
        logger.info(
            "Policy v2 rule triggered",
            rule="system_detected",
            decision=decision,
        )
        return WeeklyPolicyResult(decision=decision, reason=reason)

    # Rule 6: Fallback (delegate to v1)
    logger.debug("Policy v2: falling back to v1 default behavior")
    return v1_result
