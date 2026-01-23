"""Plan Inspector service for diagnostic/debugging views.

Gathers plan intent, phase logic, weekly structure, coach reasoning,
and plan modifications for developer inspection.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from sqlalchemy import select

from app.coach.schemas.intent_schemas import SeasonPlan, WeeklyIntent
from app.db.models import PlannedSession, PlanRevision
from app.db.models import SeasonPlan as SeasonPlanModel
from app.db.models import WeeklyIntent as WeeklyIntentModel
from app.db.session import get_session
from app.schemas.plan_inspect import (
    CoachAssessment,
    PlanChangeEvaluation,
    PlanChangeLogItem,
    PlanChangePreviewData,
    PlanInspectResponse,
    PlanModification,
    PlanPhaseInspect,
    PlanSnapshot,
    WeekInspect,
)
from app.tools.semantic.evaluate_plan_change import evaluate_plan_change
from app.tools.semantic.preview_plan_change import preview_plan_change
from app.services.intelligence.store import IntentStore


def _get_week_start_end(week_start: date) -> tuple[date, date]:
    """Get week start and end dates (Monday to Sunday)."""
    week_end = week_start + timedelta(days=6)
    return (week_start, week_end)


def _get_week_status(week_start: date, week_end: date, today: date) -> Literal["completed", "current", "upcoming"]:
    """Determine week status: completed, current, or upcoming."""
    if today < week_start:
        return "upcoming"
    if today > week_end:
        return "completed"
    return "current"


def _extract_weekly_structure_from_plan(_plan: SeasonPlan) -> dict[str, str]:
    """Extract weekly structure pattern from season plan.

    This is a placeholder - in reality, weekly structure would come from
    the plan's phase definitions or week structures. For now, return empty dict.
    """
    return {}


def _build_plan_snapshot(
    plan: SeasonPlan,
    plan_model: SeasonPlanModel,
    _today: date,
) -> PlanSnapshot:
    """Build plan snapshot from season plan."""
    # Determine anchor type and title
    anchor_type = "race" if plan_model.primary_race_name else "objective"
    anchor_title = plan_model.primary_race_name or plan.focus
    anchor_date = plan_model.primary_race_date.date() if plan_model.primary_race_date else None

    # Calculate total weeks
    total_weeks = plan_model.total_weeks
    if not total_weeks and plan_model.start_date and plan_model.end_date:
        delta = plan_model.end_date.date() - plan_model.start_date.date()
        total_weeks = max(1, delta.days // 7)

    # Determine current phase (simplified - would need phase logic)
    current_phase = None

    weekly_structure = _extract_weekly_structure_from_plan(plan)

    return PlanSnapshot(
        objective=plan.focus,
        anchor_type=anchor_type,
        anchor_title=anchor_title,
        anchor_date=anchor_date,
        current_phase=current_phase,
        total_weeks=total_weeks or 0,
        weekly_structure=weekly_structure,
    )


def _build_week_inspect(
    week_start: date,
    week_number: int,
    weekly_intent: WeeklyIntent | None,
    planned_sessions: list[PlannedSession],
    revisions: list[PlanRevision],
    today: date,
) -> WeekInspect:
    """Build week inspection data."""
    week_end = week_start + timedelta(days=6)
    status = _get_week_status(week_start, week_end, today)

    # Extract intended focus from weekly intent
    intended_focus = weekly_intent.focus if weekly_intent else "Not specified"

    # Extract key sessions from planned sessions
    key_sessions: list[str] = []
    for session in planned_sessions:
        session_date = session.starts_at.date()
        if week_start <= session_date <= week_end:
            session_desc = f"{session.session_type or 'Session'}"
            if session.notes:
                session_desc += f": {session.notes[:50]}"
            key_sessions.append(session_desc)

    # Extract modifications for this week
    modifications: list[PlanModification] = []
    for revision in revisions:
        if (
            revision.affected_start
            and revision.affected_end
            and revision.affected_start <= week_end
            and revision.affected_end >= week_start
        ):
            mod_type = revision.revision_type.replace("modify_", "")
            delta = "Modified"
            if revision.deltas:
                delta = str(revision.deltas.get("delta", "Modified"))
            modifications.append(
                PlanModification(
                    type=mod_type,
                    affected_session=None,  # Would need to extract from deltas
                    delta=delta,
                    reason=revision.reason or "No reason provided",
                    trigger=revision.revision_type,
                )
            )

    return WeekInspect(
        week_index=week_number,
        date_range=(week_start, week_end),
        status=status,
        intended_focus=intended_focus,
        planned_key_sessions=key_sessions[:5],  # Limit to 5 key sessions
        modifications=modifications,
    )


def _build_phases(
    plan: SeasonPlan,
    plan_model: SeasonPlanModel,
    weekly_intents: list[WeeklyIntentModel],
    planned_sessions: list[PlannedSession],
    revisions: list[PlanRevision],
    today: date,
) -> list[PlanPhaseInspect]:
    """Build phase inspection data.

    This is simplified - in reality, phases would come from the plan structure.
    For now, we group weeks into phases based on week numbers.
    """
    phases: list[PlanPhaseInspect] = []

    if not plan_model.start_date:
        return phases

    start_date = plan_model.start_date.date()
    total_weeks = plan_model.total_weeks or 16

    # Group weeks into phases (simplified: Base, Build, Peak, Taper)
    phase_ranges = [
        ("Base", 0, total_weeks * 0.4),
        ("Build", total_weeks * 0.4, total_weeks * 0.75),
        ("Peak", total_weeks * 0.75, total_weeks * 0.9),
        ("Taper", total_weeks * 0.9, total_weeks),
    ]

    for phase_name, phase_start_pct, phase_end_pct in phase_ranges:
        phase_start_week = int(phase_start_pct) + 1
        phase_end_week = int(phase_end_pct)

        if phase_start_week > total_weeks:
            continue

        weeks: list[WeekInspect] = []

        for week_num in range(phase_start_week, min(phase_end_week + 1, total_weeks + 1)):
            week_start = start_date + timedelta(weeks=week_num - 1)
            week_start -= timedelta(days=week_start.weekday())  # Monday

            # Find weekly intent for this week
            weekly_intent_model = None
            for intent_model in weekly_intents:
                if intent_model.week_start.date() == week_start:
                    weekly_intent_model = intent_model
                    break

            weekly_intent = None
            if weekly_intent_model:
                try:
                    weekly_intent = WeeklyIntent(**weekly_intent_model.intent_data)
                except Exception as e:
                    logger.warning(f"Failed to parse weekly intent: {e}")

            week_inspect = _build_week_inspect(
                week_start=week_start,
                week_number=week_num,
                weekly_intent=weekly_intent,
                planned_sessions=planned_sessions,
                revisions=revisions,
                today=today,
            )
            weeks.append(week_inspect)

        if weeks:
            phases.append(
                PlanPhaseInspect(
                    name=phase_name,
                    intent=f"{phase_name} phase focusing on {plan.adaptation_goal}",
                    weeks=weeks,
                )
            )

    return phases


def _build_coach_assessment(plan: SeasonPlan) -> CoachAssessment:
    """Build coach assessment from season plan."""
    summary = plan.explanation or "No assessment available"
    confidence_score = plan.confidence or 0.5

    if confidence_score >= 0.7:
        confidence_level = "high"
    elif confidence_score >= 0.4:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    return CoachAssessment(
        summary=summary,
        confidence=confidence_level,
    )


def _build_change_log(revisions: list[PlanRevision]) -> list[PlanChangeLogItem]:
    """Build change log from plan revisions."""
    log_items: list[PlanChangeLogItem] = []

    for revision in revisions:
        change_date = revision.created_at.date()
        change_type = revision.revision_type
        description = revision.reason or f"{change_type} modification"

        log_items.append(
            PlanChangeLogItem(
                date=change_date,
                change_type=change_type,
                description=description,
            )
        )

    return log_items


async def inspect_plan(
    athlete_id: int,
    user_id: str,
    horizon: str | None = None,
    preview: bool = False,
) -> PlanInspectResponse:
    """Inspect a plan for diagnostic purposes.

    Args:
        athlete_id: Athlete ID
        user_id: User ID (for authorization)

    Returns:
        PlanInspectResponse with all inspection data

    Raises:
        ValueError: If no plan found
    """
    today = datetime.now(timezone.utc).date()

    with get_session() as session:
        # Get latest season plan
        store = IntentStore()
        plan_model = store.get_latest_season_plan(athlete_id, active_only=False)

        if not plan_model:
            raise ValueError("No season plan found for athlete")

        try:
            plan = SeasonPlan(**plan_model.plan_data)
        except Exception as e:
            logger.exception(f"Failed to parse season plan: {e}")
            raise ValueError("Failed to parse season plan data") from e

        # Get weekly intents
        weekly_intent_models = list(
            session.execute(
                select(WeeklyIntentModel)
                .where(WeeklyIntentModel.athlete_id == athlete_id)
                .order_by(WeeklyIntentModel.week_start)
            ).scalars().all()
        )

        # Get planned sessions
        if plan_model.start_date and plan_model.end_date:
            start_datetime = datetime.combine(plan_model.start_date.date(), datetime.min.time()).replace(tzinfo=timezone.utc)
            end_datetime = datetime.combine(plan_model.end_date.date(), datetime.max.time()).replace(tzinfo=timezone.utc)

            planned_sessions = list(
                session.execute(
                    select(PlannedSession)
                    .where(
                        PlannedSession.user_id == user_id,
                        PlannedSession.starts_at >= start_datetime,
                        PlannedSession.starts_at <= end_datetime,
                    )
                    .order_by(PlannedSession.starts_at)
                ).scalars().all()
            )
        else:
            planned_sessions = []

        # Get plan revisions
        revisions = list(
            session.execute(
                select(PlanRevision)
                .where(PlanRevision.athlete_id == athlete_id)
                .order_by(PlanRevision.created_at.desc())
            ).scalars().all()
        )

        # Build response
        plan_snapshot = _build_plan_snapshot(plan, plan_model, today)
        phases = _build_phases(plan, plan_model, weekly_intent_models, planned_sessions, revisions, today)
        coach_assessment = _build_coach_assessment(plan)
        change_log = _build_change_log(revisions)

        # Find current week
        current_week: WeekInspect | None = None
        for phase in phases:
            for week in phase.weeks:
                if week.status == "current":
                    current_week = week
                    break
            if current_week:
                break

        # Evaluate plan change if horizon provided
        plan_change_evaluation: PlanChangeEvaluation | None = None
        preview_data: PlanChangePreviewData | None = None

        if horizon and horizon in ("week", "season", "race"):
            try:
                eval_result = await evaluate_plan_change(
                    user_id=user_id,
                    athlete_id=athlete_id,
                    horizon=horizon,  # type: ignore
                    today=today,
                )
                plan_change_evaluation = PlanChangeEvaluation(
                    decision=eval_result.decision.decision,
                    reasons=eval_result.decision.reasons,
                    recommended_actions=eval_result.decision.recommended_actions,
                    confidence=eval_result.decision.confidence,
                    current_state_summary=eval_result.current_state_summary,
                )

                # Generate preview if requested and evaluation suggests changes
                if preview and eval_result.decision.decision != "no_change":
                    # Get last proposal (simplified - would need proposal storage)
                    # For now, create a minimal proposal based on evaluation
                    proposal = {
                        "type": "adjustment" if eval_result.decision.decision == "minor_adjustment" else "modification",
                        "affected_sessions": [],
                        "new_session": {},
                    }

                    preview_result = await preview_plan_change(
                        user_id=user_id,
                        athlete_id=athlete_id,
                        proposal=proposal,
                        horizon=horizon,  # type: ignore
                        today=today,
                    )

                    preview_data = PlanChangePreviewData(
                        change_summary=preview_result.change_summary,
                        sessions_changed_count=len(preview_result.sessions_changed),
                        key_sessions_changed=preview_result.key_sessions_changed,
                        risk_notes=preview_result.risk_notes,
                        expected_impact=preview_result.expected_impact,
                    )
            except Exception as e:
                logger.exception(f"Failed to evaluate/preview plan change: {e}")
                # Continue without evaluation/preview on error

        return PlanInspectResponse(
            plan_snapshot=plan_snapshot,
            phases=phases,
            current_week=current_week,
            coach_assessment=coach_assessment,
            change_log=change_log,
            plan_change_evaluation=plan_change_evaluation,
            preview=preview_data,
        )
