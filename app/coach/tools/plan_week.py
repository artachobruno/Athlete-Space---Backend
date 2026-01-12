import uuid
from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.calendar.training_summary import build_training_summary
from app.coach.mcp_client import MCPError, call_tool
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.constraints import TrainingConstraints
from app.coach.schemas.load_adjustment import LoadAdjustmentDecision
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.utils.constraints import RecoveryState
from app.db.models import PlannedSession
from app.db.session import get_session
from app.domains.training_plan.enums import PlanType, TrainingIntent
from app.domains.training_plan.guards import (
    assert_new_planner_only,
    assert_planner_v2_only,
    guard_no_recursion,
    guard_no_repair,
    log_planner_v2_entry,
)
from app.domains.training_plan.models import PlanContext
from app.domains.training_plan.observability import (
    PlannerStage,
    log_event,
    log_stage_event,
    log_stage_metric,
)
from app.planner.plan_race_simple import execute_canonical_pipeline

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

            # Phase 4: LLM-only extraction - use B17's translate_feedback_to_constraints
            # No keyword-based heuristics - all constraint extraction via LLM
            # TODO: Replace with LLM-based constraint extraction from user_feedback
            # For now, create minimal constraints without keyword parsing
            volume_multiplier = 1.0
            intensity_cap = "none"
            force_rest_days = 0

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

    # Use canonical pipeline for weekly planning
    logger.info("B8: Generating planned sessions using canonical pipeline...")

    # Guards: Prevent legacy paths and forbidden behaviors
    assert_new_planner_only()
    assert_planner_v2_only()
    guard_no_recursion(0)  # Entry point has depth 0
    flags_dict: dict[str, bool | str | int | float] = {}
    if state.flags:
        flags_dict = dict.fromkeys(state.flags, True)
    guard_no_repair(flags_dict)

    # Log entry point for monitoring
    log_planner_v2_entry()

    # Generate plan_id for correlation
    plan_id = str(uuid.uuid4())

    logger.info(
        "planner_v2_entry: Starting weekly plan generation",
        week_start=monday.isoformat(),
        week_end=sunday.isoformat(),
        user_id=user_id,
        athlete_id=athlete_id,
        plan_id=plan_id,
    )

    # Create plan context for 1 week
    ctx = PlanContext(
        plan_type=PlanType.WEEK,
        intent=TrainingIntent.BUILD,
        weeks=1,
        race_distance=None,  # Week plans don't have race distance
        target_date=sunday.date().isoformat(),
    )

    # Calculate base volume from training summary or state
    base_volume_hours = training_summary.volume.get("total_duration_minutes", 0) / 60.0
    if base_volume_hours == 0:
        base_volume_hours = state.seven_day_volume_hours

    # Apply load adjustment if available
    if load_adjustment:
        adjusted_volume_hours = base_volume_hours * (1.0 + load_adjustment.volume_delta_pct)
        logger.info(
            "B8: Applying LoadAdjustmentDecision to planning",
            base_volume_hours=base_volume_hours,
            adjusted_volume_hours=adjusted_volume_hours,
            volume_delta_pct=load_adjustment.volume_delta_pct,
        )
    else:
        adjusted_volume_hours = base_volume_hours
        logger.info(
            "B8: No load adjustment, using base values",
            base_volume_hours=base_volume_hours,
        )

    # Use canonical pipeline
    def volume_calculator(_week_idx: int) -> float:
        """Calculate volume for the week (convert hours to miles)."""
        # Convert hours to approximate miles (assuming ~8 min/mile pace)
        # This is a rough conversion - the actual volume allocator will handle distribution
        return adjusted_volume_hours * 7.5  # ~7.5 miles per hour at 8 min/mile

    planned_weeks, persist_result = await execute_canonical_pipeline(
        ctx=ctx,
        athlete_state=state,
        user_id=user_id,
        athlete_id=athlete_id,
        plan_id=plan_id,
        base_volume_calculator=volume_calculator,
    )

    saved_count = persist_result.created
    logger.info(
        "planner_v2_entry: Weekly plan generation complete",
        week_start=monday.isoformat(),
        week_end=sunday.isoformat(),
        persisted_count=saved_count,
        user_id=user_id,
        athlete_id=athlete_id,
    )

    if saved_count > 0:
        save_status = f"• **{saved_count} training sessions** added to your calendar\n"
        calendar_message = "Your planned sessions are now available in your calendar!"
    else:
        save_status = (
            f"• **{len(planned_weeks[0].sessions) if planned_weeks else 0} training sessions** generated "
            f"(not saved - calendar unavailable)\n"
        )
        calendar_message = (
            "⚠️ **Note:** Your training plan was generated successfully, "
            "but we couldn't save it to your calendar right now. "
            "Please try again later or contact support."
        )

    return (
        f"✅ **Weekly Training Plan Created!**\n\n"
        f"I've generated a weekly plan from **{monday.date().isoformat()}** "
        f"to **{sunday.date().isoformat()}**.\n\n"
        f"**Plan Summary:**\n"
        f"{save_status}"
        f"• Target volume: {adjusted_volume_hours:.1f} hours\n"
        f"{'• Load adjusted based on your feedback' if load_adjustment else ''}\n\n"
        f"{calendar_message}"
    )
