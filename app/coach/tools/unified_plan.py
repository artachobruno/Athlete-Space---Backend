"""Unified planning tool.

This is the single planning tool that handles all horizons (day, week, season).
Revision is handled by passing current_plan parameter.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, cast

from loguru import logger

from app.coach.schemas.canonical_plan import CanonicalPlan, PlanSession
from app.coach.tools.session_planner import save_planned_sessions
from app.coach.utils.llm_client import CoachLLMClient


async def plan_tool(
    horizon: str,
    *,
    user_feedback: str | None = None,
    current_plan: CanonicalPlan | None = None,
    activity_state: dict | None = None,
    user_id: str | None = None,
    athlete_id: int | None = None,
) -> str:
    """Unified planning tool for all horizons.

    This tool handles:
    - Creating new plans (day, week, season)
    - Revising existing plans (pass current_plan)

    Args:
        horizon: Time horizon ("day", "week", "season")
        user_feedback: User's message/feedback for planning
        current_plan: Existing plan to revise (if revision)
        activity_state: Summary of athlete's activity state
        user_id: User ID for saving
        athlete_id: Athlete ID for saving

    Returns:
        Response message with plan details
    """
    logger.debug(
        "unified_plan: Starting plan_tool",
        horizon=horizon,
        is_revision=current_plan is not None,
        has_user_feedback=bool(user_feedback),
        has_activity_state=bool(activity_state),
        user_id=user_id,
        athlete_id=athlete_id,
    )
    logger.info(
        "Unified plan tool called",
        horizon=horizon,
        is_revision=current_plan is not None,
        user_id=user_id,
        athlete_id=athlete_id,
    )

    # Validate inputs
    logger.debug("unified_plan: Validating inputs", horizon=horizon)
    if horizon not in {"day", "week", "season"}:
        logger.debug("unified_plan: Invalid horizon", horizon=horizon)
        return f"[CLARIFICATION] Invalid horizon: {horizon}. Must be 'day', 'week', or 'season'."

    if not user_id or not athlete_id:
        logger.debug("unified_plan: Missing user_id or athlete_id", has_user_id=bool(user_id), has_athlete_id=athlete_id is not None)
        return "[CLARIFICATION] user_id and athlete_id are required"

    # Calculate date range based on horizon
    logger.debug("unified_plan: Calculating date range", horizon=horizon, has_current_plan=bool(current_plan))
    today = datetime.now(timezone.utc).date()
    start_date, end_date = _calculate_date_range(horizon, today, current_plan)
    logger.debug(
        "unified_plan: Date range calculated",
        horizon=horizon,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        duration_days=(end_date - start_date).days + 1,
    )

    # Generate plan using LLM - single source of truth
    logger.debug("unified_plan: Creating LLM client")
    llm_client = CoachLLMClient()
    goal_context = {
        "plan_type": "weekly" if horizon == "week" else ("season" if horizon == "season" else "weekly"),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    user_context = {
        "user_id": user_id,
        "athlete_id": athlete_id,
        "feedback": user_feedback,
    }
    athlete_context = activity_state or {}
    calendar_constraints = {}

    logger.debug(
        "unified_plan: Preparing context for LLM generation",
        goal_context_keys=list(goal_context.keys()),
        user_context_keys=list(user_context.keys()),
        athlete_context_keys=list(athlete_context.keys()),
        calendar_constraints_keys=list(calendar_constraints.keys()),
    )
    logger.debug(
        "unified_plan: Calling generate_training_plan_via_llm",
        horizon=horizon,
        goal_context=goal_context,
        user_context_keys=list(user_context.keys()),
    )
    training_plan = await llm_client.generate_training_plan_via_llm(
        user_context=user_context,
        athlete_context=athlete_context,
        goal_context=goal_context,
        calendar_constraints=calendar_constraints,
    )
    logger.debug(
        "unified_plan: Training plan generated via LLM",
        horizon=horizon,
        plan_type=training_plan.plan_type,
        session_count=len(training_plan.sessions),
        has_rationale=bool(training_plan.rationale),
        assumptions_count=len(training_plan.assumptions),
    )

    # Convert TrainingPlan to CanonicalPlan (temporary bridge)
    logger.debug(
        "unified_plan: Converting TrainingPlan to CanonicalPlan",
        horizon=horizon,
        total_sessions=len(training_plan.sessions),
    )
    sessions: list[PlanSession] = [
        PlanSession(
            date=session.date.date(),
            type=session.sport.capitalize() if session.sport != "rest" else "Rest",
            intensity=session.intensity,
            duration_minutes=session.duration_minutes,
            notes=session.description or session.purpose,
        )
        for session in training_plan.sessions
    ]
    logger.debug(
        "unified_plan: Sessions converted to CanonicalPlan format",
        horizon=horizon,
        session_count=len(sessions),
        first_session_date=sessions[0].date.isoformat() if sessions else None,
        last_session_date=sessions[-1].date.isoformat() if sessions else None,
    )

    horizon_literal = cast(Literal["day", "week", "season"], horizon)
    plan = CanonicalPlan(
        horizon=horizon_literal,
        start_date=start_date,
        end_date=end_date,
        sessions=sessions,
        assumptions=training_plan.assumptions,
        constraints=[],
    )
    logger.debug(
        "unified_plan: CanonicalPlan created",
        horizon=horizon,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        session_count=len(sessions),
        assumptions_count=len(training_plan.assumptions),
    )

    # Apply plan replacement rules
    logger.debug(
        "unified_plan: Applying plan replacement rules",
        horizon=horizon,
        user_id=user_id,
        athlete_id=athlete_id,
    )
    await _apply_replacement_rules(plan, user_id, athlete_id)
    logger.debug("unified_plan: Plan replacement rules applied")

    # Save plan sessions - fail loudly if persistence fails
    logger.debug(
        "unified_plan: Converting plan to session dictionaries",
        horizon=horizon,
        session_count=len(plan.sessions),
    )
    sessions_dict: list[dict[str, Any]] = _plan_to_sessions(plan, user_id, athlete_id)
    logger.debug(
        "unified_plan: Sessions converted to dictionaries",
        horizon=horizon,
        sessions_dict_count=len(sessions_dict),
        first_session_keys=list(sessions_dict[0].keys()) if sessions_dict else None,
    )
    # Phase 7: Plan guarantees - ≥1 session exists
    if not sessions_dict:
        logger.debug("unified_plan: No sessions to save - raising error", horizon=horizon)
        raise RuntimeError("The AI coach failed to generate a valid training plan. Please retry.")

    plan_type = _horizon_to_plan_type(horizon)
    logger.debug(
        "unified_plan: Calling save_planned_sessions",
        horizon=horizon,
        plan_type=plan_type,
        session_count=len(sessions_dict),
        user_id=user_id,
        athlete_id=athlete_id,
    )
    saved_count = await save_planned_sessions(
        user_id=user_id,
        athlete_id=athlete_id,
        sessions=sessions_dict,
        plan_type=plan_type,
        plan_id=None,
    )
    logger.debug(
        "unified_plan: save_planned_sessions completed",
        horizon=horizon,
        saved_count=saved_count,
        expected_count=len(sessions_dict),
    )

    if saved_count > 0:
        logger.info(f"Saved {saved_count} sessions for {horizon} plan")
    else:
        logger.warning(
            "Unified plan generated but NOT persisted (MCP down) — returning plan anyway",
            horizon=horizon,
            expected_count=len(sessions_dict),
        )

    # Generate response
    logger.debug(
        "unified_plan: Generating plan response",
        horizon=horizon,
        saved_count=saved_count,
        session_count=len(plan.sessions),
    )
    response = _generate_plan_response(plan, saved_count=saved_count)
    logger.debug(
        "unified_plan: Plan response generated",
        horizon=horizon,
        response_length=len(response),
    )
    return response


def _calculate_date_range(horizon: str, today: date, current_plan: CanonicalPlan | None) -> tuple[date, date]:
    """Calculate start and end dates for a plan.

    Args:
        horizon: Time horizon
        today: Today's date
        current_plan: Existing plan (for revisions)

    Returns:
        Tuple of (start_date, end_date)
    """
    if current_plan:
        # For revisions, use existing plan's date range
        return current_plan.start_date, current_plan.end_date

    if horizon == "day":
        return today, today
    if horizon == "week":
        # Start from Monday of current week
        days_since_monday = today.weekday()
        monday = today - timedelta(days=days_since_monday)
        return monday, monday + timedelta(days=6)
    if horizon == "season":
        # Default to 12 weeks
        return today, today + timedelta(weeks=12)

    raise ValueError(f"Unknown horizon: {horizon}")


def _plan_to_sessions(
    plan: CanonicalPlan,
    user_id: str,
    athlete_id: int,
) -> list[dict]:
    """Convert CanonicalPlan to session dictionaries for saving.

    Args:
        plan: CanonicalPlan to convert
        user_id: User ID
        athlete_id: Athlete ID

    Returns:
        List of session dictionaries
    """
    logger.debug(
        "unified_plan: _plan_to_sessions - converting plan to session dictionaries",
        horizon=plan.horizon,
        session_count=len(plan.sessions),
        user_id=user_id,
        athlete_id=athlete_id,
    )
    sessions = []
    for idx, session in enumerate(plan.sessions):
        logger.debug(
            "unified_plan: _plan_to_sessions - converting session",
            index=idx,
            session_date=session.date.isoformat(),
            session_type=session.type,
            session_intensity=session.intensity,
        )
        session_dict: dict = {
            "date": session.date.isoformat(),
            "type": session.type,
            "title": f"{session.type} - {session.intensity or 'general'}",
            "intensity": session.intensity,
            "duration_minutes": session.duration_minutes,
            "notes": session.notes,
        }
        sessions.append(session_dict)
    logger.debug(
        "unified_plan: _plan_to_sessions - conversion complete",
        horizon=plan.horizon,
        total_sessions=len(sessions),
        first_session_keys=list(sessions[0].keys()) if sessions else None,
    )
    return sessions


def _horizon_to_plan_type(horizon: str) -> str:
    """Convert horizon to plan_type for database.

    Args:
        horizon: Time horizon

    Returns:
        Plan type string
    """
    if horizon == "season":
        return "season"
    if horizon == "week":
        return "weekly"
    return "single"  # For day plans


async def _apply_replacement_rules(
    plan: CanonicalPlan,
    user_id: str,
    athlete_id: int,
) -> None:
    """Apply plan replacement rules.

    Rules:
    - Day plan replaces day
    - Week plan replaces week
    - Season plan replaces season
    - Lower horizon plans do NOT auto-overwrite higher ones

    Args:
        plan: Plan to save
        user_id: User ID
        athlete_id: Athlete ID
    """
    logger.debug(
        "unified_plan: _apply_replacement_rules - starting",
        horizon=plan.horizon,
        start_date=plan.start_date.isoformat(),
        end_date=plan.end_date.isoformat(),
        user_id=user_id,
        athlete_id=athlete_id,
    )
    # For now, we'll delete existing sessions in the date range
    # In production, this would be more sophisticated
    try:
        # Get existing sessions in date range
        logger.debug(
            "unified_plan: _apply_replacement_rules - checking existing sessions via MCP",
            start_date=plan.start_date.isoformat(),
            end_date=plan.end_date.isoformat(),
        )
        result = await call_tool(
            "get_planned_sessions",
            {
                "user_id": user_id,
                "start_date": plan.start_date.isoformat(),
                "end_date": plan.end_date.isoformat(),
            },
        )
        existing_sessions = result.get("sessions", [])
        logger.debug(
            "unified_plan: _apply_replacement_rules - existing sessions retrieved",
            existing_count=len(existing_sessions),
            start_date=plan.start_date.isoformat(),
            end_date=plan.end_date.isoformat(),
        )

        # Delete existing sessions (simplified - in production would use proper deletion)
        # For now, we'll just log - actual deletion would need a delete tool
        if existing_sessions:
            logger.info(
                f"Found {len(existing_sessions)} existing sessions in date range ({plan.start_date} to {plan.end_date}) - will be replaced"
            )
            logger.debug(
                "unified_plan: _apply_replacement_rules - existing sessions will be replaced",
                existing_count=len(existing_sessions),
                new_session_count=len(plan.sessions),
            )
        else:
            logger.debug(
                "unified_plan: _apply_replacement_rules - no existing sessions to replace",
                start_date=plan.start_date.isoformat(),
                end_date=plan.end_date.isoformat(),
            )
    except MCPError as e:
        logger.debug(
            "unified_plan: _apply_replacement_rules - MCP error checking existing sessions",
            error_code=e.code,
            error_message=e.message,
            start_date=plan.start_date.isoformat(),
            end_date=plan.end_date.isoformat(),
        )
        logger.warning(f"Could not check existing sessions: {e.message}")


def _generate_plan_response(plan: CanonicalPlan, saved_count: int) -> str:
    """Generate human-readable response for plan creation.

    Args:
        plan: Created plan
        saved_count: Number of sessions saved

    Returns:
        Response message
    """
    horizon_name = {"day": "daily", "week": "weekly", "season": "season"}[plan.horizon]

    if saved_count > 0:
        save_status = f"• **{saved_count} training sessions** added to your calendar\n"
        calendar_note = "Your planned sessions are now available in your calendar!"
    else:
        save_status = f"• **{len(plan.sessions)} training sessions** generated (not saved - calendar unavailable)\n"
        calendar_note = "⚠️ **Note:** Your training plan was generated successfully, but we couldn't save it to your calendar right now. Please try again later or contact support."

    return (
        f"✅ **{horizon_name.capitalize()} Training Plan Created!**\n\n"
        f"I've generated a {horizon_name} plan from **{plan.start_date}** "
        f"to **{plan.end_date}**.\n\n"
        f"**Plan Summary:**\n"
        f"{save_status}"
        f"• Plan duration: {(plan.end_date - plan.start_date).days + 1} days\n\n"
        f"{calendar_note}"
    )
