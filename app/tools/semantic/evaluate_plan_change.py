"""Evaluate whether plan changes are needed.

Tier 2 - Decision tool (non-mutating).
Evaluates current plan state and determines if changes are recommended.

Note:
No behavior decisions are made here.
This file only computes state under Planning Model B+.
Policy lives separately.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError

from app.db.models import DailyTrainingLoad, PlanEvaluation, RacePlan, UserSettings
from app.db.session import get_session
from app.tools.interfaces import PlannedSession
from app.tools.read.activities import get_completed_activities
from app.tools.read.plans import get_planned_activities
from app.tools.read.risk import get_risk_flags
from app.utils.calendar import week_end, week_start


class PlanChangeDecision(BaseModel):
    """Decision output from evaluate_plan_change."""

    decision: Literal["no_change", "minor_adjustment", "modification_required"]
    reasons: list[str]
    recommended_actions: list[str] | None = None
    confidence: float  # 0.0-1.0


class PlanStateSummary(BaseModel):
    """Structured summary of current plan state (Planning Model B+)."""

    planned_total_week: int
    planned_elapsed: int
    planned_remaining: int
    executed_elapsed: int
    compliance_rate: float
    summary_text: str
    # Policy v1 signals (optional, populated when available)
    phase: str | None = None  # e.g., "taper", "build", "base"
    days_to_race: int | None = None
    injury_status: str | None = None  # "none", "managing", "injured"
    subjective_fatigue: str | None = None  # "low", "medium", "high"
    atl: float | None = None  # Acute Training Load
    ctl: float | None = None  # Chronic Training Load
    # Policy v4 signals (optional, populated when available)
    plan_changes_last_21_days: int | None = None
    user_intent_strength: Literal["weak", "medium", "strong"] | None = None
    experience_level: Literal["beginner", "intermediate", "advanced"] | None = None


class EvaluatePlanChangeResult(BaseModel):
    """Result from evaluate_plan_change tool."""

    decision: PlanChangeDecision
    current_state_summary: str  # Kept for backward compatibility
    current_state: PlanStateSummary
    horizon: str


def _planned_elapsed_remaining(
    planned: list[PlannedSession],
    today: date,
) -> tuple[list[PlannedSession], list[PlannedSession]]:
    """Split planned sessions into elapsed (date < today) vs remaining (date >= today)."""
    elapsed = [s for s in planned if s.date < today]
    remaining = [s for s in planned if s.date >= today]
    return elapsed, remaining


def _get_race_info(user_id: str, athlete_id: int, today: date) -> tuple[int | None, str | None]:
    """Get days_to_race and phase from active race plan.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        today: Current date

    Returns:
        Tuple of (days_to_race, phase) or (None, None) if no active race
    """
    with get_session() as session:
        # Convert today to datetime for comparison with race_date (DateTime field)
        today_dt = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)

        # Get most recent active race plan (future date)
        race_plan = session.execute(
            select(RacePlan)
            .where(
                RacePlan.user_id == user_id,
                RacePlan.athlete_id == athlete_id,
                RacePlan.race_date >= today_dt,
            )
            .order_by(RacePlan.race_date)
            .limit(1)
        ).scalar_one_or_none()

        if not race_plan:
            return None, None

        # Extract date from race_date (which is a DateTime)
        race_date = race_plan.race_date.date() if isinstance(race_plan.race_date, datetime) else race_plan.race_date
        days_to_race = (race_date - today).days

        # Determine phase based on days_to_race
        # Taper typically starts 2-3 weeks before race
        if days_to_race <= 14:
            phase = "taper"
        elif days_to_race <= 42:
            phase = "peak"
        elif days_to_race <= 84:
            phase = "build"
        else:
            phase = "base"

        return days_to_race, phase


def _get_injury_status(user_id: str) -> str | None:
    """Get injury status from user settings.

    Args:
        user_id: User ID

    Returns:
        Injury status ("none", "managing", "injured") or None
    """
    with get_session() as session:
        settings = session.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        ).scalar_one_or_none()

        if not settings or not settings.preferences:
            return None

        prefs = settings.preferences
        injury_history = prefs.get("injury_history")
        injury_notes = prefs.get("injury_notes")

        if injury_history is True:
            if injury_notes:
                return "managing"
            return "injured"
        if injury_history is False:
            return "none"

        return None


def _get_training_load_metrics(user_id: str, today: date) -> tuple[float | None, float | None, str | None]:
    """Get ATL, CTL, and infer subjective fatigue from training load.

    Args:
        user_id: User ID
        today: Current date

    Returns:
        Tuple of (atl, ctl, subjective_fatigue) or (None, None, None) if unavailable
    """
    with get_session() as session:
        # Get most recent training load metrics
        load_record = session.execute(
            select(DailyTrainingLoad)
            .where(
                DailyTrainingLoad.user_id == user_id,
                DailyTrainingLoad.day <= today,
            )
            .order_by(DailyTrainingLoad.day.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not load_record:
            return None, None, None

        atl = float(load_record.atl) if load_record.atl is not None else None
        ctl = float(load_record.ctl) if load_record.ctl is not None else None

        # Infer subjective fatigue from TSB (CTL - ATL)
        # Negative TSB = high fatigue, positive = fresh
        subjective_fatigue = None
        if atl is not None and ctl is not None:
            tsb = ctl - atl
            if tsb < -15:  # High fatigue threshold
                subjective_fatigue = "high"
            elif tsb < -5:
                subjective_fatigue = "medium"
            else:
                subjective_fatigue = "low"

        return atl, ctl, subjective_fatigue


def evaluate_plan_change(
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

    # Calendar window: week uses Mon-Sun; season/race use today + N days
    if horizon == "week":
        start_date = week_start(today)
        end_date = week_end(today)
        activity_days = 7
    elif horizon == "season":
        start_date = today
        end_date = today + timedelta(days=90)
        activity_days = 30
    else:  # race
        start_date = today
        end_date = today + timedelta(days=180)
        activity_days = 30

    # Planned intent (full window, include completed so past planned sessions exist)
    planned_week = get_planned_activities(
        user_id=user_id,
        start=start_date,
        end=end_date,
        include_completed=True,
    )
    planned_elapsed_list, planned_remaining_list = _planned_elapsed_remaining(
        planned_week, today
    )
    planned_elapsed_count = len(planned_elapsed_list)
    planned_remaining_count = len(planned_remaining_list)
    planned_total = len(planned_week)

    # Executed reality (window start through today)
    start_dt = datetime.combine(start_date, datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    end_dt = datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc)
    executed_elapsed_list = get_completed_activities(
        user_id=user_id,
        start=start_dt,
        end=end_dt,
    )
    executed_elapsed_count = len(executed_elapsed_list)

    # Compliance: elapsed plan only (Model B+)
    compliance_rate = executed_elapsed_count / max(1, planned_elapsed_count)

    logger.info(
        "Evaluation window (B+)",
        start=start_date.isoformat(),
        end=end_date.isoformat(),
        planned_total=planned_total,
        planned_elapsed=planned_elapsed_count,
        planned_remaining=planned_remaining_count,
        executed_elapsed=executed_elapsed_count,
        compliance_rate=compliance_rate,
    )

    # Recent activities (separate window for activity-pattern checks)
    recent_start = datetime.combine(
        today - timedelta(days=activity_days), datetime.min.time()
    ).replace(tzinfo=timezone.utc)
    recent_activities = get_completed_activities(
        user_id=user_id,
        start=recent_start,
        end=end_dt,
    )

    risk_flags_list = get_risk_flags(
        user_id=user_id,
        start=start_date,
        end=end_date,
    )

    # Decision logic (v1 - simple deterministic). No new behavior rules.
    decision: Literal["no_change", "minor_adjustment", "modification_required"] = (
        "no_change"
    )
    reasons: list[str] = []
    recommended_actions: list[str] = []

    if compliance_rate < 0.7:
        decision = "modification_required"
        reasons.append(f"Low compliance rate: {compliance_rate:.0%}")
        recommended_actions.append("Adjust plan to match actual execution patterns")

    has_high_risk = any(flag.get("severity") == "high" for flag in risk_flags_list)
    if has_high_risk:
        decision = "modification_required"
        reasons.append("High risk flags detected")
        high_risk_items = [
            flag.get("description", "")
            for flag in risk_flags_list
            if flag.get("severity") == "high"
        ]
        if high_risk_items:
            recommended_actions.extend([f"Address: {item}" for item in high_risk_items[:3]])

    if recent_activities and len(recent_activities) < 3:
        if decision == "no_change":
            decision = "minor_adjustment"
        reasons.append("Low activity volume in recent period")

    state_summary = (
        f"Plan evaluation for {horizon}: {planned_total} planned in window, "
        f"elapsed={planned_elapsed_count} remaining={planned_remaining_count}, "
        f"executed_elapsed={executed_elapsed_count}, "
        f"compliance: {compliance_rate:.0%}"
    )

    confidence = 0.8
    if planned_total == 0:
        confidence = 0.5
    if not recent_activities:
        confidence = 0.6

    # Populate Policy v1 signals (optional, fail gracefully if unavailable)
    days_to_race, phase = _get_race_info(user_id, athlete_id, today)
    injury_status = _get_injury_status(user_id)
    atl, ctl, subjective_fatigue = _get_training_load_metrics(user_id, today)

    state = PlanStateSummary(
        planned_total_week=planned_total,
        planned_elapsed=planned_elapsed_count,
        planned_remaining=planned_remaining_count,
        executed_elapsed=executed_elapsed_count,
        compliance_rate=compliance_rate,
        summary_text=state_summary,
        phase=phase,
        days_to_race=days_to_race,
        injury_status=injury_status,
        subjective_fatigue=subjective_fatigue,
        atl=atl,
        ctl=ctl,
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

    # Store evaluation (gracefully handle missing table)
    try:
        with get_session() as session:
            evaluation = PlanEvaluation(
                user_id=user_id,
                athlete_id=athlete_id,
                plan_version=None,
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
    except ProgrammingError as e:
        # Table doesn't exist yet - log but don't fail
        # This allows the evaluation to proceed even if migrations haven't been run
        if "does not exist" in str(e).lower() or "undefinedtable" in str(e).lower():
            logger.warning(
                "plan_evaluations table does not exist - skipping storage",
                horizon=horizon,
                user_id=user_id,
                athlete_id=athlete_id,
            )
        else:
            # Re-raise if it's a different database error
            raise

    return result
