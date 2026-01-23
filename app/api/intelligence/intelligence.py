"""Read-only API endpoints for training intelligence.

Frontend reads only. No writes.
Never expose prompts or internal context.
"""

from datetime import date, datetime, timedelta, timezone
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import func, select

from app.api.dependencies.auth import get_current_user_id
from app.api.schemas.season import SeasonSummary
from app.coach.schemas.contracts import (
    DailyDecisionListItem,
    DailyDecisionResponse,
    SeasonPlanListItem,
    SeasonPlanResponse,
    WeeklyIntentListItem,
    WeeklyIntentResponse,
    WeeklyReportListItem,
    WeeklyReportResponse,
)
from app.coach.schemas.intent_schemas import Confidence, DailyDecision, SeasonPlan, WeeklyIntent, WeeklyReport
from app.db.models import Activity, StravaAccount
from app.db.models import DailyDecision as DailyDecisionModel
from app.db.models import SeasonPlan as SeasonPlanModel
from app.db.models import WeeklyIntent as WeeklyIntentModel
from app.db.models import WeeklyReport as WeeklyReportModel
from app.db.session import get_session
from app.services.intelligence.context_builder import build_daily_decision_context
from app.services.intelligence.failures import IntelligenceFailureHandler
from app.services.intelligence.store import IntentStore
from app.services.intelligence.triggers import RegenerationTriggers
from app.services.season_summary import build_season_summary

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

store = IntentStore()
failure_handler = IntelligenceFailureHandler()
triggers = RegenerationTriggers()


async def _generate_daily_decision_on_demand(
    user_id: str,
    athlete_id: int,
    decision_date: date,
    decision_date_dt: datetime,
) -> DailyDecisionModel:
    """Generate daily decision on-demand if missing.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        decision_date: Decision date
        decision_date_dt: Decision date as datetime

    Returns:
        DailyDecisionModel if successfully generated

    Raises:
        HTTPException: If generation fails or decision cannot be retrieved
    """
    logger.info(
        f"[DAILY_DECISION] On-demand generation triggered: user_id={user_id}, "
        f"athlete_id={athlete_id}, decision_date={decision_date.isoformat()}"
    )

    try:
        # Build context for daily decision
        context = build_daily_decision_context(user_id, athlete_id, decision_date)

        # Get weekly intent ID if available
        week_start = decision_date - timedelta(days=decision_date.weekday())
        week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        weekly_intent_model = store.get_latest_weekly_intent(athlete_id, week_start_dt, active_only=True)
        weekly_intent_id = weekly_intent_model.id if weekly_intent_model else None

        # Generate and save the decision
        decision_id = await triggers.maybe_regenerate_daily_decision(
            user_id=user_id,
            athlete_id=athlete_id,
            decision_date=decision_date,
            context=context,
            weekly_intent_id=weekly_intent_id,
        )

        if decision_id is None:
            # Generation was skipped (context unchanged), but we still don't have a decision
            _raise_unavailable_error(decision_date)

        # Type narrowing: decision_id is now guaranteed to be str (not None)
        # After the None check above, decision_id is definitely a str
        # Retrieve the newly created decision by ID (more reliable than querying by date)
        decision_model = store.get_daily_decision_by_id(decision_id)
        if decision_model is not None:
            logger.info(
                f"[DAILY_DECISION] On-demand generation completed: decision_id={decision_id}, "
                f"user_id={user_id}, date={decision_date.isoformat()}"
            )
            return decision_model

        # Fallback: try querying by date in case of timing issues
        logger.warning(f"[DAILY_DECISION] Decision {decision_id} not found by ID, trying date query as fallback")
        decision_model = store.get_latest_daily_decision(user_id, decision_date_dt, active_only=True)
        if decision_model is not None:
            logger.info(
                f"[DAILY_DECISION] On-demand generation completed (via fallback): decision_id={decision_id}, "
                f"user_id={user_id}, date={decision_date.isoformat()}"
            )
            return decision_model

        # If we get here, neither query found the decision
        logger.error(
            f"[DAILY_DECISION] Decision was generated (decision_id={decision_id}) but could not be retrieved. "
            f"This should not happen."
        )
        _raise_unavailable_error(decision_date)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(
            f"[DAILY_DECISION] On-demand generation failed: user_id={user_id}, "
            f"athlete_id={athlete_id}, decision_date={decision_date.isoformat()}"
        )
        # Diagnostic: Check if user has activities/data available
        with get_session() as session:
            activity_count = session.execute(select(func.count()).select_from(Activity).where(Activity.user_id == user_id)).scalar() or 0

        logger.warning(
            f"[DAILY_DECISION] Generation failed diagnostics: user_id={user_id}, athlete_id={athlete_id}, "
            f"decision_date={decision_date.isoformat()}, activity_count={activity_count}"
        )

        raise HTTPException(
            status_code=503,
            detail=f"Daily decision not available for {decision_date.isoformat()}. The coach will generate it soon.",
        ) from e


def _raise_unavailable_error(decision_date: date) -> NoReturn:
    """Raise HTTPException for unavailable daily decision.

    Args:
        decision_date: Decision date

    Raises:
        HTTPException: Always raises with 503 status
    """
    raise HTTPException(
        status_code=503,
        detail=f"Daily decision not available for {decision_date.isoformat()}. The coach will generate it soon.",
    )


def _get_athlete_id_from_user(user_id: str) -> int:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        user_id: Current authenticated user ID

    Returns:
        Athlete ID as integer

    Raises:
        HTTPException: If Strava account not found
    """
    with get_session() as session:
        account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Strava account not connected",
            )
        return int(account[0].athlete_id)


@router.get("/season", response_model=SeasonPlanResponse)
def get_season_plan(user_id: str = Depends(get_current_user_id)):
    """Get the latest active season plan for the current user.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Latest active SeasonPlan or 503 if unavailable

    Raises:
        HTTPException: If plan not found or Strava account not connected
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    logger.info(f"Getting season plan for user_id={user_id}, athlete_id={athlete_id}")

    plan_model = store.get_latest_season_plan(user_id=user_id, active_only=True)

    if plan_model is None:
        # Try fallback to inactive plan
        plan_model = store.get_latest_season_plan(user_id=user_id, active_only=False)
        if plan_model is None:
            raise HTTPException(
                status_code=503,
                detail="Season plan not available. The coach is still learning about your training patterns.",
            )

    try:
        plan = SeasonPlan(**plan_model.plan_data)
    except Exception as e:
        logger.exception(
            f"Failed to parse season plan (plan_id={plan_model.id}, athlete_id={athlete_id})"
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


@router.get("/season/summary", response_model=SeasonSummary)
async def get_season_summary(user_id: str = Depends(get_current_user_id)):
    """Get season narrative summary - a read-only, story-driven view.

    This endpoint returns a narrative view of how the season is unfolding
    relative to the plan, week by week. It shows phases, weeks, and coach
    summaries - no metrics, no calendar mechanics.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        SeasonSummary with phases and weeks

    Raises:
        HTTPException: If season plan not found or Strava account not connected
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    logger.info(f"Getting season summary for user_id={user_id}, athlete_id={athlete_id}")

    try:
        return await build_season_summary(user_id, athlete_id)
    except ValueError as e:
        logger.warning(f"Season summary not available: {e}")
        raise HTTPException(
            status_code=503,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception(f"Error building season summary: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to build season summary",
        ) from e


@router.get("/week", response_model=WeeklyIntentResponse)
def get_weekly_intent(
    user_id: str = Depends(get_current_user_id),
    week_start: date | None = None,
):
    """Get the latest active weekly intent for the current user.

    Args:
        user_id: Current authenticated user ID (from auth dependency)
        week_start: Week start date (Monday). If None, uses current week.

    Returns:
        Latest active WeeklyIntent for the week or 503 if unavailable

    Raises:
        HTTPException: If intent not found or Strava account not connected
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    if week_start is None:
        # Get current week start (Monday)
        today = datetime.now(timezone.utc).date()
        days_since_monday = today.weekday()
        week_start = today - timedelta(days=days_since_monday)

    logger.info(f"Getting weekly intent for user_id={user_id}, athlete_id={athlete_id}, week_start={week_start.isoformat()}")

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
        logger.exception(
            f"Failed to parse weekly intent (intent_id={intent_model.id}, athlete_id={athlete_id})"
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
async def get_daily_decision(
    user_id: str = Depends(get_current_user_id),
    decision_date: date | None = None,
):
    """Get the latest active daily decision for the current user.

    Args:
        user_id: Current authenticated user ID (from auth dependency)
        decision_date: Decision date. If None, uses today.

    Returns:
        Latest active DailyDecision for the date or empty response if unavailable

    Always returns 200 - never raises errors for missing decisions.
    """
    if decision_date is None:
        decision_date = datetime.now(timezone.utc).date()

    logger.info(f"[DAILY_DECISION] API request: user_id={user_id}, decision_date={decision_date.isoformat()}")

    # Check if user has any activities (graceful short-circuit)
    with get_session() as session:
        activity_count = session.execute(select(func.count()).select_from(Activity).where(Activity.user_id == user_id)).scalar() or 0
        if activity_count == 0:
            logger.debug(f"No activities found for user_id={user_id}, returning empty decision")
            # Return empty but valid response
            return DailyDecisionResponse(
                id="",
                user_id=user_id,
                decision=DailyDecision(
                    recommendation="rest",
                    volume_hours=None,
                    intensity_focus=None,
                    session_type="Rest day",
                    risk_level="none",
                    risk_notes=None,
                    confidence=Confidence(score=0.0, explanation="No training data available yet"),
                    explanation="No training data available yet. The coach will provide recommendations once you start logging activities.",
                    decision_date=decision_date,
                    weekly_intent_id=None,
                ),
                weekly_intent_id=None,
                version=0,
                is_active=False,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

    decision_date_dt = datetime.combine(decision_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    decision_model = store.get_latest_daily_decision(user_id, decision_date_dt, active_only=True)

    if decision_model is None:
        # Try fallback to inactive decision
        decision_model = store.get_latest_daily_decision(user_id, decision_date_dt, active_only=False)
        if decision_model is None:
            # For today's date, attempt on-demand generation for better UX
            # Historical dates still rely on background jobs
            today = datetime.now(timezone.utc).date()
            if decision_date == today:
                try:
                    athlete_id = _get_athlete_id_from_user(user_id)
                    logger.info(
                        f"[DAILY_DECISION] No decision found for today, triggering on-demand generation: "
                        f"user_id={user_id}, athlete_id={athlete_id}"
                    )
                    decision_model = await _generate_daily_decision_on_demand(
                        user_id=user_id,
                        athlete_id=athlete_id,
                        decision_date=decision_date,
                        decision_date_dt=decision_date_dt,
                    )
                except HTTPException as http_exc:
                    # If generation fails, fall through to return placeholder
                    logger.warning(
                        f"[DAILY_DECISION] On-demand generation failed with HTTP {http_exc.status_code}: "
                        f"user_id={user_id}, decision_date={decision_date.isoformat()}, "
                        f"detail={http_exc.detail}"
                    )
                except Exception as e:
                    # Log but don't fail - return placeholder instead
                    logger.exception(
                        f"[DAILY_DECISION] Unexpected error during on-demand generation: "
                        f"user_id={user_id}, decision_date={decision_date.isoformat()}, error={e}"
                    )

            # If still no decision (either not today, or generation failed), return placeholder
            if decision_model is None:
                logger.warning(
                    f"[DAILY_DECISION] Returning placeholder: user_id={user_id}, "
                    f"date={decision_date.isoformat()}, is_today={decision_date == today}"
                )
                return DailyDecisionResponse(
                    id="",
                    user_id=user_id,
                    decision=DailyDecision(
                        recommendation="rest",
                        volume_hours=None,
                        intensity_focus=None,
                        session_type="Rest day",
                        risk_level="none",
                        risk_notes=None,
                        confidence=Confidence(score=0.0, explanation="Decision not yet generated"),
                        explanation="The coach is still analyzing your training data. Recommendations will be available soon.",
                        decision_date=decision_date,
                        weekly_intent_id=None,
                    ),
                    weekly_intent_id=None,
                    version=0,
                    is_active=False,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )

    try:
        decision = DailyDecision(**decision_model.decision_data)
    except Exception:
        logger.warning(
            f"Failed to parse daily decision (decision_id={decision_model.id}, user_id={user_id}), returning empty response"
        )
        # Return empty response instead of raising 500
        return DailyDecisionResponse(
            id=str(decision_model.id),
            user_id=user_id,
            decision=DailyDecision(
                recommendation="rest",
                volume_hours=None,
                intensity_focus=None,
                session_type="Rest day",
                risk_level="none",
                risk_notes=None,
                confidence=Confidence(score=0.0, explanation="Failed to parse decision data"),
                explanation="Unable to load decision data. Please try again later.",
                decision_date=decision_date,
                weekly_intent_id=str(decision_model.weekly_intent_id) if decision_model.weekly_intent_id else None,
            ),
            weekly_intent_id=str(decision_model.weekly_intent_id) if decision_model.weekly_intent_id else None,
            version=decision_model.version,
            is_active=decision_model.is_active,
            created_at=decision_model.created_at,
            updated_at=decision_model.updated_at,
        )

    return DailyDecisionResponse(
        id=str(decision_model.id),
        user_id=decision_model.user_id,
        decision=decision,
        weekly_intent_id=str(decision_model.weekly_intent_id) if decision_model.weekly_intent_id else None,
        version=decision_model.version,
        is_active=decision_model.is_active,
        created_at=decision_model.created_at,
        updated_at=decision_model.updated_at,
    )


@router.get("/week-report", response_model=WeeklyReportResponse)
def get_weekly_report(
    user_id: str = Depends(get_current_user_id),
    week_start: date | None = None,
):
    """Get the latest active weekly report for the current user.

    Args:
        user_id: Current authenticated user ID (from auth dependency)
        week_start: Week start date (Monday). If None, uses previous week.

    Returns:
        Latest active WeeklyReport for the week or 503 if unavailable

    Raises:
        HTTPException: If report not found or Strava account not connected
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    if week_start is None:
        # Get previous week (reports are generated at end of week)
        today = datetime.now(timezone.utc).date()
        days_since_monday = today.weekday()
        current_week_start = today - timedelta(days=days_since_monday)
        week_start = current_week_start - timedelta(days=7)

    logger.info(f"Getting weekly report for user_id={user_id}, athlete_id={athlete_id}, week_start={week_start.isoformat()}")

    week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    report_model = store.get_latest_weekly_report(athlete_id, week_start_dt, active_only=True)

    if report_model is None:
        # Try fallback to inactive report
        report_model = store.get_latest_weekly_report(athlete_id, week_start_dt, active_only=False)
        if report_model is None:
            raise HTTPException(
                status_code=503,
                detail=f"Weekly report not available for week starting {week_start.isoformat()}. The coach will generate it soon.",
            )

    try:
        report = WeeklyReport(**report_model.report_data)
    except Exception as e:
        logger.exception(
            f"Failed to parse weekly report (report_id={report_model.id}, athlete_id={athlete_id})"
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to parse weekly report data",
        ) from e

    return WeeklyReportResponse(
        id=report_model.id,
        user_id=report_model.user_id,
        athlete_id=report_model.athlete_id,
        report=report,
        version=report_model.version,
        is_active=report_model.is_active,
        created_at=report_model.created_at,
        updated_at=report_model.updated_at,
    )


# List endpoints (using metadata fields for fast queries)


@router.get("/season/list", response_model=list[SeasonPlanListItem])
def list_season_plans(
    user_id: str = Depends(get_current_user_id),
    limit: int = 10,
    active_only: bool = True,
):
    """Get list of season plans (metadata only, fast query).

    Args:
        user_id: Current authenticated user ID
        limit: Maximum number of plans to return (default: 10)
        active_only: If True, only return active plans

    Returns:
        List of season plan metadata (no full JSON payload)
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    logger.info(f"Listing season plans for user_id={user_id}, athlete_id={athlete_id}, limit={limit}")

    with get_session() as session:
        query = select(SeasonPlanModel).where(SeasonPlanModel.athlete_id == athlete_id)

        if active_only:
            query = query.where(SeasonPlanModel.is_active.is_(True))

        plans = session.execute(query.order_by(SeasonPlanModel.version.desc()).limit(limit)).scalars().all()

        return [
            SeasonPlanListItem(
                id=plan.id,
                plan_name=plan.plan_name,
                start_date=plan.start_date,
                end_date=plan.end_date,
                primary_race_date=plan.primary_race_date,
                primary_race_name=plan.primary_race_name,
                total_weeks=plan.total_weeks,
                version=plan.version,
                is_active=plan.is_active,
                created_at=plan.created_at,
            )
            for plan in plans
        ]


@router.get("/week/list", response_model=list[WeeklyIntentListItem])
def list_weekly_intents(
    user_id: str = Depends(get_current_user_id),
    limit: int = 10,
    active_only: bool = True,
):
    """Get list of weekly intents (metadata only, fast query).

    Args:
        user_id: Current authenticated user ID
        limit: Maximum number of intents to return (default: 10)
        active_only: If True, only return active intents

    Returns:
        List of weekly intent metadata (no full JSON payload)
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    logger.info(f"Listing weekly intents for user_id={user_id}, athlete_id={athlete_id}, limit={limit}")

    with get_session() as session:
        query = select(WeeklyIntentModel).where(WeeklyIntentModel.athlete_id == athlete_id)

        if active_only:
            query = query.where(WeeklyIntentModel.is_active.is_(True))

        intents = session.execute(query.order_by(WeeklyIntentModel.week_start.desc()).limit(limit)).scalars().all()

        return [
            WeeklyIntentListItem(
                id=intent.id,
                week_start=intent.week_start,
                week_number=intent.week_number,
                primary_focus=intent.primary_focus,
                total_sessions=intent.total_sessions,
                target_volume_hours=intent.target_volume_hours,
                season_plan_id=intent.season_plan_id,
                version=intent.version,
                is_active=intent.is_active,
                created_at=intent.created_at,
            )
            for intent in intents
        ]


@router.get("/decisions/list", response_model=list[DailyDecisionListItem])
def list_daily_decisions(
    user_id: str = Depends(get_current_user_id),
    limit: int = 30,
    active_only: bool = True,
):
    """Get list of daily decisions (metadata only, fast query).

    Args:
        user_id: Current authenticated user ID
        limit: Maximum number of decisions to return (default: 30)
        active_only: If True, only return active decisions

    Returns:
        List of daily decision metadata (no full JSON payload)
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    logger.info(f"Listing daily decisions for user_id={user_id}, athlete_id={athlete_id}, limit={limit}")

    with get_session() as session:
        query = select(DailyDecisionModel).where(DailyDecisionModel.user_id == user_id)

        if active_only:
            query = query.where(DailyDecisionModel.is_active.is_(True))

        decisions = session.execute(query.order_by(DailyDecisionModel.decision_date.desc()).limit(limit)).scalars().all()

        return [
            DailyDecisionListItem(
                id=decision.id,
                decision_date=decision.decision_date,
                recommendation_type=decision.recommendation_type,
                recommended_intensity=decision.recommended_intensity,
                has_workout=decision.has_workout,
                weekly_intent_id=decision.weekly_intent_id,
                version=decision.version,
                is_active=decision.is_active,
                created_at=decision.created_at,
            )
            for decision in decisions
        ]


@router.get("/week-report/list", response_model=list[WeeklyReportListItem])
def list_weekly_reports(
    user_id: str = Depends(get_current_user_id),
    limit: int = 10,
    active_only: bool = True,
):
    """Get list of weekly reports (metadata only, fast query).

    Args:
        user_id: Current authenticated user ID
        limit: Maximum number of reports to return (default: 10)
        active_only: If True, only return active reports

    Returns:
        List of weekly report metadata (no full JSON payload)
    """
    athlete_id = _get_athlete_id_from_user(user_id)
    logger.info(f"Listing weekly reports for user_id={user_id}, athlete_id={athlete_id}, limit={limit}")

    with get_session() as session:
        query = select(WeeklyReportModel).where(WeeklyReportModel.athlete_id == athlete_id)

        if active_only:
            query = query.where(WeeklyReportModel.is_active.is_(True))

        reports = session.execute(query.order_by(WeeklyReportModel.week_start.desc()).limit(limit)).scalars().all()

        return [
            WeeklyReportListItem(
                id=report.id,
                week_start=report.week_start,
                week_end=report.week_end,
                summary_score=report.summary_score,
                key_insights_count=report.key_insights_count,
                activities_completed=report.activities_completed,
                adherence_percentage=report.adherence_percentage,
                version=report.version,
                is_active=report.is_active,
                created_at=report.created_at,
            )
            for report in reports
        ]
