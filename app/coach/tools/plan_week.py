from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.calendar.training_summary import build_training_summary
from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.constraints import TrainingConstraints
from app.coach.schemas.load_adjustment import LoadAdjustmentDecision
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.session_planner import save_planned_sessions
from app.coach.utils.constraints import RecoveryState
from app.db.models import PlannedSession
from app.db.session import get_session

# Cache to prevent duplicate calls within a short time window
_recent_calls: dict[str, datetime] = {}


def _check_weekly_plan_exists(user_id: str | None, athlete_id: int | None) -> bool:
    """Check if planned sessions exist for the current week.

    Args:
        user_id: User ID (optional)
        athlete_id: Athlete ID (optional)

    Returns:
        True if planned sessions exist for current week, False otherwise
    """
    if user_id is None or athlete_id is None:
        return False

    try:
        now = datetime.now(timezone.utc)
        # Get Monday of current week
        days_since_monday = now.weekday()
        monday = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

        with get_session() as session:
            result = session.execute(
                select(PlannedSession)
                .where(
                    PlannedSession.user_id == user_id,
                    PlannedSession.athlete_id == athlete_id,
                    PlannedSession.date >= monday,
                    PlannedSession.date <= sunday,
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None
    except Exception as e:
        logger.warning(f"Error checking for existing weekly plan: {e}")
        return False


async def plan_week(
    state: AthleteState,
    user_id: str | None = None,
    athlete_id: int | None = None,
    user_feedback: str | None = None,
) -> str:
    """B8: Unified Planning Tool - Create weekly planned sessions.

    Consumes:
    - TrainingSummary (B16)
    - TrainingConstraints (B17) - if user feedback provided
    - LoadAdjustmentDecision (B18) - computed from B16 + B17

    Produces:
    - Planned sessions saved to calendar

    Args:
        state: Current athlete state with load metrics and trends
        user_id: User ID (required for saving sessions)
        athlete_id: Athlete ID (required for saving sessions)
        user_feedback: Optional user feedback for constraint generation

    Returns:
        Success message with session count
    """
    logger.info(
        "B8: Unified planning tool called",
        tsb=state.tsb,
        load_trend=state.load_trend,
        flags=state.flags,
        has_feedback=user_feedback is not None,
    )

    if not user_id or not athlete_id:
        return "[CLARIFICATION] user_id and athlete_id are required for planning"

    # Idempotency check: if weekly plan already exists, return early
    if _check_weekly_plan_exists(user_id, athlete_id):
        logger.info("Weekly plan already exists for current week, returning early")
        return "Your weekly plan is already created."

    if state.confidence < 0.1:
        return "[CLARIFICATION] athlete_state_confidence_low"

    # B16: Build TrainingSummary
    logger.info("B8: Building TrainingSummary (B16)...")
    try:
        training_summary = build_training_summary(
            user_id=user_id,
            athlete_id=athlete_id,
            window_days=14,
        )
        logger.info(
            "B8: TrainingSummary built",
            compliance=training_summary.execution.get("compliance_rate", 0.0),
            sessions_completed=training_summary.execution.get("completed_sessions", 0),
        )
    except Exception as e:
        logger.error(f"B8: Failed to build TrainingSummary: {e}", exc_info=True)
        return f"[CLARIFICATION] Failed to build training summary: {e}"

    # B17 + B18: Build constraints and load adjustment if feedback provided
    load_adjustment: LoadAdjustmentDecision | None = None
    if user_feedback:
        logger.info(
            "B8: User feedback provided, computing constraints (B17) and load adjustment (B18)...",
            user_feedback=user_feedback[:200],  # Log first 200 chars
        )
        try:
            # Build RecoveryState from state
            recovery_state = RecoveryState(
                atl=state.atl,
                tsb=state.tsb,
                recovery_status="over" if state.tsb < -25.0 else ("under" if state.tsb > 5.0 else "adequate"),
                risk_flags=list(state.flags),
            )

            # For now, we'll create basic constraints from feedback keywords
            # In production, this would use B17's translate_feedback_to_constraints
            # Simple keyword-based constraint detection
            feedback_lower = user_feedback.lower()
            volume_multiplier = 1.0
            intensity_cap = "none"
            force_rest_days = 0

            if any(word in feedback_lower for word in ["fatigue", "tired", "exhausted", "worn"]):
                volume_multiplier = 0.8
                intensity_cap = "moderate"
            if any(word in feedback_lower for word in ["sore", "pain", "hurt"]):
                volume_multiplier = 0.7
                intensity_cap = "easy"
                force_rest_days = 1
            if any(word in feedback_lower for word in ["wrecked", "destroyed", "can't"]):
                volume_multiplier = 0.6
                intensity_cap = "easy"
                force_rest_days = 2

            constraints = TrainingConstraints(
                volume_multiplier=volume_multiplier,
                intensity_cap=intensity_cap,
                force_rest_days=force_rest_days,
                disallow_intensity_days=set(),
                long_session_cap_minutes=None,
                expiry_date=datetime.now(timezone.utc).date() + timedelta(days=7),
                source="user_feedback",
                confidence=0.7,
                reason_codes=[],
                explanation=f"Constraints derived from feedback: {user_feedback[:100]}",
                created_at=datetime.now(timezone.utc),
            )

            # B18: Compute load adjustment
            logger.info(
                "B8: Calling B18 adjust_training_load",
                constraints_volume_multiplier=constraints.volume_multiplier,
                constraints_intensity_cap=constraints.intensity_cap,
                constraints_force_rest_days=constraints.force_rest_days,
            )
            load_adjustment = adjust_training_load(
                training_summary=training_summary,
                recovery_state=recovery_state,
                constraints=constraints,
            )
            logger.info(
                "B8: LoadAdjustmentDecision computed (B18 output)",
                volume_delta_pct=load_adjustment.volume_delta_pct,
                intensity_cap=load_adjustment.intensity_cap,
                forced_rest_days=load_adjustment.forced_rest_days,
                effective_window_days=load_adjustment.effective_window_days,
                reason_codes=[code.value for code in load_adjustment.reason_codes],
                confidence=load_adjustment.confidence,
            )
        except Exception as e:
            logger.warning(f"B8: Failed to compute constraints/adjustment, using defaults: {e}")
            load_adjustment = None

    # Calculate week dates
    now = datetime.now(timezone.utc)
    days_since_monday = now.weekday()
    monday = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

    # B8: Generate planned sessions using adjusted values
    logger.info("B8: Generating planned sessions...")

    # Base volume from training summary or state
    base_volume_hours = training_summary.volume.get("total_duration_minutes", 0) / 60.0
    if base_volume_hours == 0:
        # Fallback to state
        base_volume_hours = state.seven_day_volume_hours

    # Apply load adjustment if available
    if load_adjustment:
        adjusted_volume_hours = base_volume_hours * (1.0 + load_adjustment.volume_delta_pct)
        intensity_cap = load_adjustment.intensity_cap
        forced_rest_days = set(load_adjustment.forced_rest_days)
        logger.info(
            "B8: Applying LoadAdjustmentDecision to planning",
            base_volume_hours=base_volume_hours,
            adjusted_volume_hours=adjusted_volume_hours,
            volume_delta_pct=load_adjustment.volume_delta_pct,
            intensity_cap=intensity_cap,
            forced_rest_days_count=len(forced_rest_days),
        )
    else:
        adjusted_volume_hours = base_volume_hours
        intensity_cap = "none"
        forced_rest_days = set()
        logger.info(
            "B8: No load adjustment, using base values",
            base_volume_hours=base_volume_hours,
        )

    # Generate sessions (5-7 sessions per week)
    sessions = []
    current_date = monday.date()
    session_count = 0

    # Distribute sessions across the week
    for day_offset in range(7):
        session_date = current_date + timedelta(days=day_offset)
        date_str = session_date.isoformat()

        # Skip forced rest days
        if date_str in forced_rest_days:
            sessions.append({
                "date": session_date.isoformat(),
                "type": "Rest",
                "title": "Rest Day",
                "intensity": "rest",
                "notes": "Recovery day",
            })
            continue

        # Skip if we've generated enough sessions
        if session_count >= 7:
            break

        # Determine session type based on day and constraints
        if day_offset in {1, 3}:  # Tuesday, Thursday - quality days
            if intensity_cap == "easy":
                intensity = "easy"
                duration_minutes = 45
                title = "Easy Run"
            elif intensity_cap == "moderate":
                intensity = "moderate"
                duration_minutes = 50
                title = "Moderate Run"
            else:
                intensity = "hard"
                duration_minutes = 60
                title = "Hard Workout"
        else:  # Other days - easy runs
            intensity = "easy"
            duration_minutes = int((adjusted_volume_hours * 60) / max(5, 1))
            duration_minutes = max(30, min(duration_minutes, 90))
            title = "Easy Run"

        sessions.append({
            "date": session_date.isoformat(),
            "type": "Run",
            "title": title,
            "duration_minutes": duration_minutes,
            "intensity": intensity,
            "notes": "Weekly training session",
        })
        session_count += 1

    # Save sessions via MCP
    logger.info(
        "B8: Saving planned sessions",
        session_count=len(sessions),
        week_start=monday.date().isoformat(),
        week_end=sunday.date().isoformat(),
    )
    try:
        saved_count = await save_planned_sessions(
            user_id=user_id,
            athlete_id=athlete_id,
            sessions=sessions,
            plan_type="weekly",
            plan_id=None,
        )
        logger.info(
            "B8: Planned sessions saved successfully",
            saved_count=saved_count,
            session_details=[{"date": s["date"], "title": s["title"], "intensity": s.get("intensity")} for s in sessions[:5]],
        )
    except Exception as e:
        logger.error(f"B8: Failed to save sessions: {e}", exc_info=True)
        return f"[CLARIFICATION] Failed to save planned sessions: {e}"

    # Generate response
    return (
        f"✅ **Weekly Training Plan Created!**\n\n"
        f"I've generated a weekly plan from **{monday.date().isoformat()}** "
        f"to **{sunday.date().isoformat()}**.\n\n"
        f"**Plan Summary:**\n"
        f"• **{saved_count} training sessions** added to your calendar\n"
        f"• Target volume: {adjusted_volume_hours:.1f} hours\n"
        f"{'• Load adjusted based on your feedback' if load_adjustment else ''}\n\n"
        f"Your planned sessions are now available in your calendar!"
    )
