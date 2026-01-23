"""Evaluate whether plan changes are needed.

Tier 2 - Decision tool (non-mutating).
Evaluates current plan state and determines if changes are recommended.
"""

from datetime import date
from typing import Literal

from loguru import logger
from pydantic import BaseModel

from app.tools.read.activities import get_recent_activities
from app.tools.read.compliance import get_plan_compliance
from app.tools.read.plans import get_planned_sessions
from app.tools.read.risk import get_risk_flags


class PlanChangeDecision(BaseModel):
    """Decision output from evaluate_plan_change."""

    decision: Literal["no_change", "minor_adjustment", "modification_required"]
    reasons: list[str]
    recommended_actions: list[str] | None = None
    confidence: float  # 0.0-1.0


class EvaluatePlanChangeResult(BaseModel):
    """Result from evaluate_plan_change tool."""

    decision: PlanChangeDecision
    current_state_summary: str
    horizon: str


async def evaluate_plan_change(
    user_id: str,
    athlete_id: int,
    horizon: Literal["week", "season", "race"],
    today: date | None = None,
) -> EvaluatePlanChangeResult:
    """Evaluate whether plan changes are needed.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        horizon: Time horizon to evaluate
        today: Current date (defaults to today)

    Returns:
        EvaluatePlanChangeResult with decision and reasoning
    """
    if today is None:
        from datetime import date as date_today

        today = date_today()

    logger.info(
        "Evaluating plan change",
        user_id=user_id,
        athlete_id=athlete_id,
        horizon=horizon,
    )

    # Gather data
    planned_sessions = await get_planned_sessions(
        user_id=user_id,
        athlete_id=athlete_id,
        start_date=today,
        end_date=None,  # Will be filtered by horizon
    )

    recent_activities = await get_recent_activities(
        user_id=user_id,
        athlete_id=athlete_id,
        days=7 if horizon == "week" else 30,
    )

    compliance = await get_plan_compliance(
        user_id=user_id,
        athlete_id=athlete_id,
        horizon=horizon,
    )

    risk_flags = await get_risk_flags(
        user_id=user_id,
        athlete_id=athlete_id,
    )

    # Decision logic (v1 - simple deterministic)
    decision: Literal["no_change", "minor_adjustment", "modification_required"] = "no_change"
    reasons: list[str] = []
    recommended_actions: list[str] = []

    # Check compliance
    if compliance.compliance_rate < 0.7:
        decision = "modification_required"
        reasons.append(f"Low compliance rate: {compliance.compliance_rate:.0%}")
        recommended_actions.append("Adjust plan to match actual execution patterns")

    # Check risk flags
    if risk_flags.has_high_risk:
        decision = "modification_required"
        reasons.append("High risk flags detected")
        if risk_flags.risk_items:
            recommended_actions.extend([f"Address: {item}" for item in risk_flags.risk_items[:3]])

    # Check activity patterns
    if recent_activities and len(recent_activities) < 3:
        if decision == "no_change":
            decision = "minor_adjustment"
        reasons.append("Low activity volume in recent period")

    # Build summary
    state_summary = f"Plan evaluation for {horizon}: {len(planned_sessions)} sessions planned, "
    state_summary += f"{len(recent_activities)} recent activities, "
    state_summary += f"compliance: {compliance.compliance_rate:.0%}"

    # Confidence based on data quality
    confidence = 0.8
    if not planned_sessions:
        confidence = 0.5
    if not recent_activities:
        confidence = 0.6

    return EvaluatePlanChangeResult(
        decision=PlanChangeDecision(
            decision=decision,
            reasons=reasons if reasons else ["No changes needed at this time"],
            recommended_actions=recommended_actions if recommended_actions else None,
            confidence=confidence,
        ),
        current_state_summary=state_summary,
        horizon=horizon,
    )
