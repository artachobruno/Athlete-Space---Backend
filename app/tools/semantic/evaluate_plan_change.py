"""Evaluate whether plan changes are needed.

Tier 2 - Decision tool (non-mutating).
Evaluates current plan state and determines if changes are recommended.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import PlanEvaluation
from app.db.session import get_session
from app.tools.read.activities import get_completed_activities
from app.tools.read.compliance import get_plan_compliance
from app.tools.read.plans import get_planned_activities
from app.tools.read.risk import get_risk_flags


class PlanChangeDecision(BaseModel):
    """Decision output from evaluate_plan_change."""

    decision: Literal["no_change", "minor_adjustment", "modification_required"]
    reasons: list[str]
    recommended_actions: list[str] | None = None
    confidence: float  # 0.0-1.0


class PlanStateSummary(BaseModel):
    """Structured summary of current plan state."""

    total_planned_sessions: int
    total_recent_activities: int
    compliance_rate: float
    summary_text: str


class EvaluatePlanChangeResult(BaseModel):
    """Result from evaluate_plan_change tool."""

    decision: PlanChangeDecision
    current_state_summary: str  # Kept for backward compatibility
    current_state: PlanStateSummary  # New structured summary
    horizon: str


async def evaluate_plan_change(  # noqa: RUF029
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
        today = datetime.now(timezone.utc).date()

    logger.info(
        "Evaluating plan change",
        user_id=user_id,
        athlete_id=athlete_id,
        horizon=horizon,
    )

    # Calculate date range based on horizon
    if horizon == "week":
        start_date = today
        end_date = today + timedelta(days=7)
        activity_days = 7
    elif horizon == "season":
        start_date = today
        end_date = today + timedelta(days=90)
        activity_days = 30
    else:  # race
        start_date = today
        end_date = today + timedelta(days=180)
        activity_days = 30

    # Gather data (these are sync functions, not async)
    planned_sessions = get_planned_activities(
        user_id=user_id,
        start=start_date,
        end=end_date,
    )

    # Get recent activities (needs datetime, not date)
    start_datetime = datetime.combine(today - timedelta(days=activity_days), datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    end_datetime = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
    recent_activities = get_completed_activities(
        user_id=user_id,
        start=start_datetime,
        end=end_datetime,
    )

    compliance = get_plan_compliance(
        user_id=user_id,
        start=start_date,
        end=end_date,
    )

    risk_flags_list = get_risk_flags(
        user_id=user_id,
        start=start_date,
        end=end_date,
    )

    # Decision logic (v1 - simple deterministic)
    decision: Literal["no_change", "minor_adjustment", "modification_required"] = "no_change"
    reasons: list[str] = []
    recommended_actions: list[str] = []

    # Check compliance (compliance is a dict)
    compliance_rate = compliance.get("completion_pct", 1.0)
    if compliance_rate < 0.7:
        decision = "modification_required"
        reasons.append(f"Low compliance rate: {compliance_rate:.0%}")
        recommended_actions.append("Adjust plan to match actual execution patterns")

    # Check risk flags (risk_flags is a list of dicts)
    has_high_risk = any(flag.get("severity") == "high" for flag in risk_flags_list)
    if has_high_risk:
        decision = "modification_required"
        reasons.append("High risk flags detected")
        high_risk_items = [flag.get("description", "") for flag in risk_flags_list if flag.get("severity") == "high"]
        if high_risk_items:
            recommended_actions.extend([f"Address: {item}" for item in high_risk_items[:3]])

    # Check activity patterns
    if recent_activities and len(recent_activities) < 3:
        if decision == "no_change":
            decision = "minor_adjustment"
        reasons.append("Low activity volume in recent period")

    # Build summary
    state_summary = f"Plan evaluation for {horizon}: {len(planned_sessions)} sessions planned, "
    state_summary += f"{len(recent_activities)} recent activities, "
    state_summary += f"compliance: {compliance_rate:.0%}"

    # Confidence based on data quality
    confidence = 0.8
    if not planned_sessions:
        confidence = 0.5
    if not recent_activities:
        confidence = 0.6

    # Build structured summary
    state = PlanStateSummary(
        total_planned_sessions=len(planned_sessions),
        total_recent_activities=len(recent_activities),
        compliance_rate=compliance_rate,
        summary_text=state_summary,
    )

    result = EvaluatePlanChangeResult(
        decision=PlanChangeDecision(
            decision=decision,
            reasons=reasons if reasons else ["No changes needed at this time"],
            recommended_actions=recommended_actions if recommended_actions else None,
            confidence=confidence,
        ),
        current_state_summary=state_summary,
        current_state=state,
        horizon=horizon,
    )

    # Store evaluation for auditability and reuse
    with get_session() as session:
        evaluation = PlanEvaluation(
            user_id=user_id,
            athlete_id=athlete_id,
            plan_version=None,  # TODO: Link to season_plan_id when available
            horizon=horizon,
            decision=decision,
            reasons=reasons if reasons else ["No changes needed at this time"],
            recommended_actions=recommended_actions if recommended_actions else None,
            confidence=confidence,
            current_state_summary=state_summary,
        )
        session.add(evaluation)
        session.commit()
        logger.info(
            "Plan evaluation stored",
            evaluation_id=evaluation.id,
            horizon=horizon,
            decision=decision,
        )

    return result
