"""Read-only API endpoints for training intelligence.

Frontend reads only. No writes.
Never expose prompts or internal context.
"""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.coach.contracts import DailyDecisionResponse, SeasonPlanResponse, WeeklyIntentResponse
from app.coach.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent
from app.intelligence.failures import IntelligenceFailureHandler
from app.intelligence.store import IntentStore

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

store = IntentStore()
failure_handler = IntelligenceFailureHandler()


@router.get("/season", response_model=SeasonPlanResponse)
def get_season_plan(athlete_id: int):
    """Get the latest active season plan for an athlete.

    Returns:
        Latest active SeasonPlan or 503 if unavailable

    Raises:
        HTTPException: If plan not found or athlete_id invalid
    """
    logger.info("Getting season plan", athlete_id=athlete_id)

    plan_model = store.get_latest_season_plan(athlete_id, active_only=True)

    if plan_model is None:
        # Try fallback to inactive plan
        plan_model = store.get_latest_season_plan(athlete_id, active_only=False)
        if plan_model is None:
            raise HTTPException(
                status_code=503,
                detail="Season plan not available. The coach is still learning about your training patterns.",
            )

    try:
        plan = SeasonPlan(**plan_model.plan_data)
    except Exception as e:
        logger.error(
            "Failed to parse season plan",
            plan_id=plan_model.id,
            athlete_id=athlete_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to parse season plan data",
        ) from e

    return SeasonPlanResponse(
        id=plan_model.id,
        user_id=plan_model.user_id,
        athlete_id=plan_model.athlete_id,
        plan=plan,
        version=plan_model.version,
        is_active=plan_model.is_active,
        created_at=plan_model.created_at,
        updated_at=plan_model.updated_at,
    )


@router.get("/week", response_model=WeeklyIntentResponse)
def get_weekly_intent(athlete_id: int, week_start: date | None = None):
    """Get the latest active weekly intent for an athlete.

    Args:
        athlete_id: Athlete ID
        week_start: Week start date (Monday). If None, uses current week.

    Returns:
        Latest active WeeklyIntent for the week or 503 if unavailable

    Raises:
        HTTPException: If intent not found or athlete_id invalid
    """
    if week_start is None:
        # Get current week start (Monday)
        today = datetime.now(timezone.utc).date()
        days_since_monday = today.weekday()
        week_start = today - timedelta(days=days_since_monday)

    logger.info("Getting weekly intent", athlete_id=athlete_id, week_start=week_start.isoformat())

    week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    intent_model = store.get_latest_weekly_intent(athlete_id, week_start_dt, active_only=True)

    if intent_model is None:
        # Try fallback to inactive intent
        intent_model = store.get_latest_weekly_intent(athlete_id, week_start_dt, active_only=False)
        if intent_model is None:
            raise HTTPException(
                status_code=503,
                detail=f"Weekly intent not available for week starting {week_start.isoformat()}. The coach will generate it soon.",
            )

    try:
        intent = WeeklyIntent(**intent_model.intent_data)
    except Exception as e:
        logger.error(
            "Failed to parse weekly intent",
            intent_id=intent_model.id,
            athlete_id=athlete_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to parse weekly intent data",
        ) from e

    return WeeklyIntentResponse(
        id=intent_model.id,
        user_id=intent_model.user_id,
        athlete_id=intent_model.athlete_id,
        intent=intent,
        season_plan_id=intent_model.season_plan_id,
        version=intent_model.version,
        is_active=intent_model.is_active,
        created_at=intent_model.created_at,
        updated_at=intent_model.updated_at,
    )


@router.get("/today", response_model=DailyDecisionResponse)
def get_daily_decision(athlete_id: int, decision_date: date | None = None):
    """Get the latest active daily decision for an athlete.

    Args:
        athlete_id: Athlete ID
        decision_date: Decision date. If None, uses today.

    Returns:
        Latest active DailyDecision for the date or 503 if unavailable

    Raises:
        HTTPException: If decision not found or athlete_id invalid
    """
    if decision_date is None:
        decision_date = datetime.now(timezone.utc).date()

    logger.info("Getting daily decision", athlete_id=athlete_id, decision_date=decision_date.isoformat())

    decision_date_dt = datetime.combine(decision_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    decision_model = store.get_latest_daily_decision(athlete_id, decision_date_dt, active_only=True)

    if decision_model is None:
        # Try fallback to inactive decision
        decision_model = store.get_latest_daily_decision(athlete_id, decision_date_dt, active_only=False)
        if decision_model is None:
            raise HTTPException(
                status_code=503,
                detail=f"Daily decision not available for {decision_date.isoformat()}. The coach will generate it soon.",
            )

    try:
        decision = DailyDecision(**decision_model.decision_data)
    except Exception as e:
        logger.error(
            "Failed to parse daily decision",
            decision_id=decision_model.id,
            athlete_id=athlete_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to parse daily decision data",
        ) from e

    return DailyDecisionResponse(
        id=decision_model.id,
        user_id=decision_model.user_id,
        athlete_id=decision_model.athlete_id,
        decision=decision,
        weekly_intent_id=decision_model.weekly_intent_id,
        version=decision_model.version,
        is_active=decision_model.is_active,
        created_at=decision_model.created_at,
        updated_at=decision_model.updated_at,
    )
