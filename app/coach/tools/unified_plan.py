"""Unified planning tool.

This is the single planning tool that handles all horizons (day, week, season).
Revision is handled by passing current_plan parameter.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, cast

from loguru import logger
from sqlalchemy import select

from app.coach.executor.errors import ExecutionError
from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.canonical_plan import CanonicalPlan, PlanSession
from app.coach.schemas.training_plan_schemas import TrainingPlan
from app.coach.tools.session_planner import save_planned_sessions
from app.coach.utils.llm_client import CoachLLMClient
from app.db.models import User
from app.db.session import get_session
from app.internal.ops.traffic import record_persistence_degraded, record_persistence_saved
from app.utils.timezone import now_user


async def plan_tool(
    horizon: str,
    *,
    user_feedback: str | None = None,
    current_plan: CanonicalPlan | None = None,
    activity_state: dict | None = None,
    user_id: str | None = None,
    athlete_id: int | None = None,
) -> dict[str, str | dict]:
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
        Dictionary with:
            - message: Response message with plan details
            - metadata: Persistence metadata (persistence_status, saved_sessions)
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
        return {
            "message": f"[CLARIFICATION] Invalid horizon: {horizon}. Must be 'day', 'week', or 'season'.",
            "metadata": {"persistence_status": "degraded", "saved_sessions": 0},
        }

    if not user_id or not athlete_id:
        logger.debug("unified_plan: Missing user_id or athlete_id", has_user_id=bool(user_id), has_athlete_id=athlete_id is not None)
        return {
            "message": "[CLARIFICATION] user_id and athlete_id are required",
            "metadata": {"persistence_status": "degraded", "saved_sessions": 0},
        }

    # Calculate date range based on horizon
    logger.debug("unified_plan: Calculating date range", horizon=horizon, has_current_plan=bool(current_plan))
    # Get today in user's timezone if user_id is available
    if user_id:
        try:
            with get_session() as session:
                user_result = session.execute(select(User).where(User.id == user_id)).first()
                if user_result:
                    user = user_result[0]
                    today = now_user(user).date()
                    logger.debug(f"unified_plan: Using user timezone {user.timezone} for date calculation")
                else:
                    today = datetime.now(timezone.utc).date()
        except Exception as e:
            logger.warning(f"unified_plan: Failed to get user timezone, using UTC: {e}")
            today = datetime.now(timezone.utc).date()
    else:
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
    result = await save_planned_sessions(
        user_id=user_id,
        athlete_id=athlete_id,
        sessions=sessions_dict,
        plan_type=plan_type,
        plan_id=None,
    )
    saved_count_raw = result.get("saved_count", 0)
    saved_count = int(saved_count_raw) if isinstance(saved_count_raw, (int, str)) else 0
    persistence_status = result.get("persistence_status", "degraded")

    logger.debug(
        "unified_plan: save_planned_sessions completed",
        horizon=horizon,
        saved_count=saved_count,
        persistence_status=persistence_status,
        expected_count=len(sessions_dict),
    )

    if persistence_status != "saved" or saved_count <= 0:
        logger.error(
            "Unified plan persistence failed — raising",
            horizon=horizon,
            persistence_status=persistence_status,
            saved_count=saved_count,
        )
        record_persistence_degraded()
        raise ExecutionError("calendar_persistence_failed")

    logger.info(f"Saved {saved_count} sessions for {horizon} plan")
    record_persistence_saved()

    # Log plan structure, weeks, overview, and summary
    _log_plan_details(training_plan, plan, horizon, user_id, athlete_id)

    # Generate response
    logger.debug(
        "unified_plan: Generating plan response",
        horizon=horizon,
        saved_count=saved_count,
        session_count=len(plan.sessions),
    )
    response_message = _generate_plan_response(plan, training_plan, saved_count=saved_count)
    logger.debug(
        "unified_plan: Plan response generated",
        horizon=horizon,
        response_length=len(response_message),
    )

    # Include persistence metadata in response
    response_metadata = {
        "persistence_status": persistence_status,
        "saved_sessions": saved_count,
    }

    # Return dict with message and metadata for structured access
    # Maintain backward compatibility by also supporting string return
    return {
        "message": response_message,
        "metadata": response_metadata,
    }


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


def _log_plan_details(
    training_plan: TrainingPlan,
    plan: CanonicalPlan,
    horizon: str,
    user_id: str | None = None,
    athlete_id: int | None = None,
) -> None:
    """Log plan structure, weeks, overview, and summary.

    Args:
        training_plan: Original TrainingPlan from LLM
        plan: CanonicalPlan representation
        horizon: Plan horizon
        user_id: User ID for logging context
        athlete_id: Athlete ID for logging context
    """
    # Extract structure: group sessions by week
    weeks_data: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "session_count": 0,
        "sessions": [],
        "types": defaultdict(int),
        "intensities": defaultdict(int),
        "total_duration": 0,
        "total_distance": 0.0,
    })

    for session in training_plan.sessions:
        week_num = session.week_number
        weeks_data[week_num]["session_count"] += 1
        weeks_data[week_num]["sessions"].append({
            "title": session.title,
            "date": session.date.isoformat() if session.date else None,
            "sport": session.sport,
            "intensity": session.intensity,
            "duration_minutes": session.duration_minutes,
            "distance_km": session.distance_km,
        })
        weeks_data[week_num]["types"][session.sport] += 1
        weeks_data[week_num]["intensities"][session.intensity] += 1
        if session.duration_minutes:
            weeks_data[week_num]["total_duration"] += session.duration_minutes
        if session.distance_km:
            weeks_data[week_num]["total_distance"] += session.distance_km

    # Convert to sorted list for logging
    weeks_list = sorted(weeks_data.items())
    total_weeks = len(weeks_list)

    # Log plan structure
    structure = {
        "total_weeks": total_weeks,
        "total_sessions": len(training_plan.sessions),
        "plan_type": training_plan.plan_type,
        "horizon": horizon,
        "start_date": plan.start_date.isoformat(),
        "end_date": plan.end_date.isoformat(),
        "duration_days": (plan.end_date - plan.start_date).days + 1,
    }
    logger.info(
        "Training plan structure",
        structure=structure,
        user_id=user_id,
        athlete_id=athlete_id,
    )

    # Log each week
    for week_num, week_data in weeks_list:
        week_info = {
            "week_number": week_num,
            "session_count": week_data["session_count"],
            "session_types": dict(week_data["types"]),
            "intensity_distribution": dict(week_data["intensities"]),
            "total_duration_minutes": week_data["total_duration"],
            "total_distance_km": week_data["total_distance"],
            "sessions": week_data["sessions"],
        }
        logger.info(
            f"Training plan week {week_num}",
            week=week_info,
            user_id=user_id,
            athlete_id=athlete_id,
        )

    # Log overview (rationale)
    logger.info(
        "Training plan overview",
        overview={
            "rationale": training_plan.rationale,
            "assumptions": training_plan.assumptions,
        },
        user_id=user_id,
        athlete_id=athlete_id,
    )

    # Generate and log summary
    summary = {
        "total_weeks": total_weeks,
        "total_sessions": len(training_plan.sessions),
        "average_sessions_per_week": round(len(training_plan.sessions) / total_weeks, 1) if total_weeks > 0 else 0,
        "plan_type": training_plan.plan_type,
        "rationale_summary": training_plan.rationale[:200] + "..." if len(training_plan.rationale) > 200 else training_plan.rationale,
    }
    logger.info(
        "Training plan summary",
        summary=summary,
        user_id=user_id,
        athlete_id=athlete_id,
    )


def _generate_plan_response(plan: CanonicalPlan, training_plan: TrainingPlan, saved_count: int) -> str:
    """Generate human-readable response for plan creation.

    Args:
        plan: Created plan
        training_plan: Original TrainingPlan from LLM
        saved_count: Number of sessions saved

    Returns:
        Response message
    """
    horizon_name = {"day": "daily", "week": "weekly", "season": "season"}[plan.horizon]

    # Extract structure: group sessions by week
    weeks_data: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "session_count": 0,
        "types": defaultdict(int),
    })

    for session in training_plan.sessions:
        week_num = session.week_number
        weeks_data[week_num]["session_count"] += 1
        weeks_data[week_num]["types"][session.sport] += 1

    weeks_list = sorted(weeks_data.items())
    total_weeks = len(weeks_list)

    # Build structure section
    structure_lines = [
        f"• **{total_weeks} weeks** of training",
        f"• **{len(training_plan.sessions)} total sessions**",
        f"• Plan type: **{training_plan.plan_type}**",
    ]

    # Build weeks section
    weeks_section = ""
    if total_weeks > 0:
        weeks_lines = []
        for week_num, week_data in weeks_list[:10]:  # Show first 10 weeks
            session_count = week_data["session_count"]
            types_str = ", ".join([f"{count} {sport}" for sport, count in sorted(week_data["types"].items())])
            weeks_lines.append(f"  **Week {week_num}**: {session_count} sessions ({types_str})")
        if total_weeks > 10:
            weeks_lines.append(f"  ... and {total_weeks - 10} more weeks")
        weeks_section = "\n".join(weeks_lines)

    # Build overview section
    overview_section = training_plan.rationale if training_plan.rationale else "No overview provided."

    # Build summary section
    avg_sessions = round(len(training_plan.sessions) / total_weeks, 1) if total_weeks > 0 else 0
    summary_lines = [
        f"• **{total_weeks} weeks** of structured training",
        f"• **{len(training_plan.sessions)} sessions** total (~{avg_sessions} per week)",
        f"• Duration: **{(plan.end_date - plan.start_date).days + 1} days**",
    ]
    if training_plan.assumptions:
        summary_lines.append(f"• **{len(training_plan.assumptions)} assumptions** considered")

    save_status = f"• **{saved_count} training sessions** added to your calendar\n"
    calendar_note = "Your planned sessions are now available in your calendar!"

    response = (
        f"✅ **{horizon_name.capitalize()} Training Plan Created!**\n\n"
        f"I've generated a {horizon_name} plan from **{plan.start_date}** "
        f"to **{plan.end_date}**.\n\n"
        f"**Plan Structure:**\n"
        + "\n".join(structure_lines) + "\n\n"
    )

    if weeks_section:
        response += f"**Weekly Breakdown:**\n{weeks_section}\n\n"

    response += (
        f"**Plan Overview:**\n{overview_section}\n\n"
        f"**Summary:**\n"
        + "\n".join(summary_lines) + "\n\n"
        f"{save_status}"
        f"{calendar_note}"
    )

    return response
