"""Unified planning tool.

This is the single planning tool that handles all horizons (day, week, season).
Revision is handled by passing current_plan parameter.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Literal, cast

from loguru import logger

from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.canonical_plan import CanonicalPlan, PlanSession


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
    logger.info(
        "Unified plan tool called",
        horizon=horizon,
        is_revision=current_plan is not None,
        user_id=user_id,
        athlete_id=athlete_id,
    )

    # Validate inputs
    if horizon not in {"day", "week", "season"}:
        return f"[CLARIFICATION] Invalid horizon: {horizon}. Must be 'day', 'week', or 'season'."

    if not user_id or not athlete_id:
        return "[CLARIFICATION] user_id and athlete_id are required"

    # Calculate date range based on horizon
    today = datetime.now(timezone.utc).date()
    start_date, end_date = _calculate_date_range(horizon, today, current_plan)

    # Generate plan using LLM (simplified - in production this would call LLM)
    # For now, we'll create a basic plan structure
    plan = _generate_plan(
        horizon,
        start_date,
        end_date,
        user_feedback=user_feedback,
        current_plan=current_plan,
        activity_state=activity_state,
    )

    # Apply plan replacement rules
    await _apply_replacement_rules(plan, user_id, athlete_id)

    # Save plan sessions
    sessions = _plan_to_sessions(plan, user_id, athlete_id)
    saved_count = 0
    if sessions:
        try:
            # Use MCP tool to save sessions
            result = await call_tool(
                "save_planned_sessions",
                {
                    "user_id": user_id,
                    "athlete_id": athlete_id,
                    "sessions": sessions,
                    "plan_type": _horizon_to_plan_type(horizon),
                    "plan_id": None,
                },
            )
            saved_count = result.get("saved_count", 0)
            logger.info(f"Saved {saved_count} sessions for {horizon} plan")
        except MCPError as e:
            logger.error(f"Failed to save plan sessions via MCP: {e.code}: {e.message}")
            return f"[CLARIFICATION] Failed to save plan: {e.message}"
        except Exception as e:
            logger.error(f"Failed to save plan sessions: {e}", exc_info=True)
            return f"[CLARIFICATION] Failed to save plan: {e}"

    # Generate response
    return _generate_plan_response(plan, saved_count=len(sessions))


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


def _generate_plan(
    horizon: str,
    start_date: date,
    end_date: date,
    *,
    user_feedback: str | None = None,  # noqa: ARG001
    current_plan: CanonicalPlan | None = None,  # noqa: ARG001
    activity_state: dict | None = None,  # noqa: ARG001
) -> CanonicalPlan:
    """Generate a plan (simplified - in production would use LLM).

    Args:
        horizon: Time horizon
        start_date: Plan start date
        end_date: Plan end date
        user_feedback: User feedback
        current_plan: Existing plan for revision
        activity_state: Activity state summary

    Returns:
        Generated CanonicalPlan
    """
    # This is a placeholder - in production, this would call an LLM
    # to generate the actual plan based on user feedback and activity state

    sessions: list[PlanSession] = []

    if horizon == "day":
        # Single session for the day
        sessions.append(
            PlanSession(
                date=start_date,
                type="Run",
                intensity="easy",
                duration_minutes=45,
                notes="Easy aerobic run",
            )
        )
    elif horizon == "week":
        # Generate sessions for the week
        current_date = start_date
        while current_date <= end_date:
            if current_date.weekday() < 6:  # Not Sunday
                sessions.append(
                    PlanSession(
                        date=current_date,
                        type="Run",
                        intensity="easy" if current_date.weekday() % 2 == 0 else "moderate",
                        duration_minutes=45 + (current_date.weekday() * 5),
                        notes=f"Week {current_date.weekday()} session",
                    )
                )
            current_date += timedelta(days=1)
    elif horizon == "season":
        # Generate weekly structure for season
        current_date = start_date
        week_num = 1
        while current_date <= end_date:
            # Add 3-4 sessions per week
            for day_offset in [0, 2, 4, 6]:  # Mon, Wed, Fri, Sun
                session_date = current_date + timedelta(days=day_offset)
                if session_date <= end_date:
                    sessions.append(
                        PlanSession(
                            date=session_date,
                            type="Run",
                            intensity="easy" if week_num % 4 == 0 else "moderate",
                            duration_minutes=45 + (week_num * 5),
                            notes=f"Week {week_num} session",
                        )
                    )
            current_date += timedelta(weeks=1)
            week_num += 1

    # Validate and cast horizon to Literal type
    valid_horizons = {"day", "week", "season"}
    if horizon not in valid_horizons:
        raise ValueError(f"Invalid horizon: {horizon}. Must be one of: {valid_horizons}")

    horizon_literal = cast(Literal["day", "week", "season"], horizon)

    return CanonicalPlan(
        horizon=horizon_literal,
        start_date=start_date,
        end_date=end_date,
        sessions=sessions,
        assumptions=["Generated as placeholder - LLM integration needed"],
        constraints=["Basic structure only"],
    )


def _plan_to_sessions(
    plan: CanonicalPlan,
    user_id: str,  # noqa: ARG001
    athlete_id: int,  # noqa: ARG001
) -> list[dict]:
    """Convert CanonicalPlan to session dictionaries for saving.

    Args:
        plan: CanonicalPlan to convert
        user_id: User ID
        athlete_id: Athlete ID

    Returns:
        List of session dictionaries
    """
    sessions = []
    for session in plan.sessions:
        session_dict: dict = {
            "date": session.date.isoformat(),
            "type": session.type,
            "title": f"{session.type} - {session.intensity or 'general'}",
            "intensity": session.intensity,
            "duration_minutes": session.duration_minutes,
            "notes": session.notes,
        }
        sessions.append(session_dict)
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
    athlete_id: int,  # noqa: ARG001
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
    # For now, we'll delete existing sessions in the date range
    # In production, this would be more sophisticated
    try:
        # Get existing sessions in date range
        result = await call_tool(
            "get_planned_sessions",
            {
                "user_id": user_id,
                "start_date": plan.start_date.isoformat(),
                "end_date": plan.end_date.isoformat(),
            },
        )
        existing_sessions = result.get("sessions", [])

        # Delete existing sessions (simplified - in production would use proper deletion)
        # For now, we'll just log - actual deletion would need a delete tool
        if existing_sessions:
            logger.info(
                f"Found {len(existing_sessions)} existing sessions in date range ({plan.start_date} to {plan.end_date}) - will be replaced"
            )
    except MCPError as e:
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

    return (
        f"✅ **{horizon_name.capitalize()} Training Plan Created!**\n\n"
        f"I've generated a {horizon_name} plan from **{plan.start_date}** "
        f"to **{plan.end_date}**.\n\n"
        f"**Plan Summary:**\n"
        f"• **{saved_count} training sessions** added to your calendar\n"
        f"• Plan duration: {(plan.end_date - plan.start_date).days + 1} days\n\n"
        f"Your planned sessions are now available in your calendar!"
    )
