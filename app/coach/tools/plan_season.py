import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import NoReturn

from loguru import logger

from app.coach.executor.errors import PersistenceError
from app.coach.schemas.athlete_state import AthleteState
from app.coach.utils.date_extraction import extract_dates_from_text
from app.domains.training_plan.enums import PlanType, TrainingIntent
from app.domains.training_plan.guards import (
    assert_new_planner_only,
    assert_planner_v2_only,
    guard_no_recursion,
    guard_no_repair,
    log_planner_v2_entry,
)
from app.domains.training_plan.models import PlanContext, PlannedSession, PlannedWeek
from app.domains.training_plan.observability import (
    PlannerStage,
    log_event,
    log_stage_event,
    log_stage_metric,
)
from app.planner.plan_race_simple import execute_canonical_pipeline

# Cache to prevent duplicate calls within a short time window
_recent_calls: dict[str, datetime] = {}


def _raise_calendar_persistence_failed() -> NoReturn:
    """Raise when calendar persistence fails; generation without persistence is failure."""
    raise PersistenceError("plan_commit_failed")


def parse_season_dates(message: str) -> tuple[datetime, datetime]:
    """Parse season start and end dates from message using LLM extraction.

    Args:
        message: Message containing season dates

    Returns:
        Tuple of (season_start, season_end) datetime objects
    """
    dates, start_date_str, end_date_str = extract_dates_from_text(
        text=message,
        context="season dates",
        expected_count=2,
    )

    # If we have at least 2 dates, use them
    if len(dates) >= 2:
        season_start = datetime.combine(dates[0], datetime.min.time()).replace(tzinfo=timezone.utc)
        season_end = datetime.combine(dates[-1], datetime.min.time()).replace(tzinfo=timezone.utc)
        return (season_start, season_end)

    # If we have start_date and end_date strings, use them
    if start_date_str and end_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str).date()
            end_date = datetime.fromisoformat(end_date_str).date()
            season_start = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            season_end = datetime.combine(end_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Failed to parse extracted date strings: {e}",
                start_date_str=start_date_str,
                end_date_str=end_date_str,
            )
        else:
            return (season_start, season_end)

    # If we have 1 date, use it as start and add 180 days
    if len(dates) == 1:
        season_start = datetime.combine(dates[0], datetime.min.time()).replace(tzinfo=timezone.utc)
        return (season_start, season_start + timedelta(days=180))

    # Default to current date + 6 months
    season_start = datetime.now(timezone.utc)
    return (season_start, season_start + timedelta(days=180))


def generate_season_plan_response(
    season_start: datetime,
    season_end: datetime,
    saved_count: int,
    weeks: int,
) -> str:
    """Generate success response for season plan creation. Caller must ensure saved_count > 0."""
    save_status = f"• **{saved_count} training sessions** added to your calendar\n"
    calendar_note = (
        "Your planned sessions are now available in your calendar! You can view them in the calendar view and track your progress."
    )

    return (
        f"✅ **Season Training Plan Created!**\n\n"
        f"I've generated a {weeks}-week season training plan from **{season_start.strftime('%B %d, %Y')}** "
        f"to **{season_end.strftime('%B %d, %Y')}**.\n\n"
        f"**Plan Summary:**\n"
        f"{save_status}"
        f"• Season duration: {weeks} weeks\n"
        f"• Phases: Base → Build → Peak → Recovery\n\n"
        f"**Training Structure:**\n"
        f"• **Base Phase**: Aerobic volume building, strength work\n"
        f"• **Build Phase**: Race-specific intensity, structured workouts\n"
        f"• **Peak Phase**: Maximum specificity, race preparation\n"
        f"• **Recovery Phase**: Active recovery, reset\n\n"
        f"{calendar_note}"
    )


async def plan_season(message: str = "", user_id: str | None = None, athlete_id: int | None = None) -> str:
    """Generate a season training plan with sessions.

    Args:
        message: Optional message with season details
        user_id: User ID for saving sessions (optional)
        athlete_id: Athlete ID for saving sessions (optional)

    Returns:
        Response message with plan details or clarification questions
    """
    logger.debug(
        "plan_season: Starting plan_season tool",
        message_length=len(message),
        has_user_id=bool(user_id),
        has_athlete_id=athlete_id is not None,
        user_id=user_id,
        athlete_id=athlete_id,
    )
    logger.info(f"Tool plan_season called (message_length={len(message)})")
    message_lower = message.lower().strip() if message else ""

    # Create a simple hash of the message for duplicate detection
    logger.debug(
        "plan_season: Checking for duplicate calls",
        message_preview=message_lower[:100] if message_lower else None,
    )
    message_hash = str(hash(message_lower[:100]))  # Use first 100 chars
    now = datetime.now(timezone.utc)

    # Check if we've been called recently with similar input (within last 10 seconds)
    if message_hash in _recent_calls:
        last_time = _recent_calls[message_hash]
        time_diff = (now - last_time).total_seconds()
        logger.debug(
            "plan_season: Duplicate call check",
            message_hash=message_hash,
            time_diff_seconds=time_diff,
            is_duplicate=time_diff < 10,
        )
        if time_diff < 10:
            logger.warning("Duplicate plan_season tool call detected within 10 seconds, blocking repeat call")
            return (
                "I've already provided information about season planning. "
                "**Please do not call this tool again with the same input.**\n\n"
                "To create a season training plan, provide both the start and end dates in your message:\n\n"
                "• **Season start date** (e.g., January 1, 2026)\n"
                "• **Season end date** (e.g., December 31, 2026)\n\n"
                'Example: "Plan my training season from January 1 to December 31, 2026"'
            )

    # Update cache
    _recent_calls[message_hash] = now
    # Clean old entries (older than 30 seconds) to prevent memory growth
    cutoff = now - timedelta(seconds=30)
    keys_to_remove = [k for k, v in _recent_calls.items() if v <= cutoff]
    logger.debug(
        "plan_season: Cleaning old cache entries",
        cache_size_before=len(_recent_calls),
        keys_to_remove_count=len(keys_to_remove),
    )
    for key in keys_to_remove:
        del _recent_calls[key]

    # Extract season dates
    logger.debug(
        "plan_season: Extracting season dates",
        message_preview=message[:200] if message else None,
    )
    season_start, season_end = parse_season_dates(message)
    logger.debug(
        "plan_season: Season dates extracted",
        season_start=season_start.isoformat(),
        season_end=season_end.isoformat(),
        season_duration_days=(season_end - season_start).days,
    )

    # Check if we need more info
    if not message or ("season" not in message_lower and "plan" not in message_lower):
        return (
            "I'd love to create a season training plan for you! To generate your plan, please provide:\n\n"
            "• **Season start date** (e.g., January 1, 2026)\n"
            "• **Season end date** (e.g., December 31, 2026)\n"
            "• **Target races** (optional): List any key races with dates\n"
            "• **Training goals** (optional): What you want to focus on this season\n\n"
            "Once you provide these details, I'll generate a complete season plan with all training sessions "
            "that will be added to your calendar."
        )

    # Generate plan via canonical pipeline if we have user_id and athlete_id
    if user_id and athlete_id:
        logger.debug(
            "plan_season: Starting canonical pipeline plan generation",
            user_id=user_id,
            athlete_id=athlete_id,
            season_start=season_start.isoformat(),
            season_end=season_end.isoformat(),
        )
        try:
            # Guards: Prevent legacy paths and forbidden behaviors
            assert_new_planner_only()
            assert_planner_v2_only()
            guard_no_recursion(0)  # Entry point has depth 0
            flags_dict: dict[str, bool | str | int | float] = {}
            guard_no_repair(flags_dict)

            # Log entry point for monitoring
            log_planner_v2_entry()

            # Generate plan_id for correlation
            plan_id = str(uuid.uuid4())

            logger.info(
                "planner_v2_entry: Starting season plan generation",
                season_start=season_start.isoformat(),
                season_end=season_end.isoformat(),
                user_id=user_id,
                athlete_id=athlete_id,
                plan_id=plan_id,
            )

            # Calculate total weeks
            total_weeks = int((season_end.date() - season_start.date()).days / 7)
            if total_weeks < 4:
                total_weeks = 12  # Default to 12 weeks for season plans
                season_end = season_start + timedelta(weeks=12)

            # Create plan context
            ctx = PlanContext(
                plan_type=PlanType.SEASON,
                intent=TrainingIntent.BUILD,
                weeks=total_weeks,
                race_distance=None,  # Season plans don't have race distance
                target_date=season_end.date().isoformat(),
            )

            # Use default athlete state
            athlete_state = AthleteState(
                ctl=50.0,
                atl=45.0,
                tsb=5.0,
                load_trend="stable",
                volatility="low",
                days_since_rest=2,
                days_to_race=None,
                seven_day_volume_hours=5.0,
                fourteen_day_volume_hours=10.0,
                flags=[],
                confidence=0.9,
            )

            # Use canonical pipeline
            # NOTE: Volume is calculated in MILES (not km). All distance calculations use miles.
            # See app/plans/README.md for volume and pace semantics.
            def volume_calculator(week_idx: int) -> float:
                """Calculate volume for a week (season plans use moderate progression).

                Returns volume in MILES. The canonical pipeline expects miles.
                """
                base_volume = 40.0  # Base volume in miles
                return base_volume + (week_idx * 1.5)  # Moderate progression for season plans

            _planned_weeks, persist_result = await execute_canonical_pipeline(
                ctx=ctx,
                athlete_state=athlete_state,
                user_id=user_id,
                athlete_id=athlete_id,
                plan_id=plan_id,
                base_volume_calculator=volume_calculator,
            )

            saved_count = persist_result.created
            logger.info(
                "planner_v2_entry: Season plan generation complete",
                season_start=season_start.isoformat(),
                season_end=season_end.isoformat(),
                total_weeks=total_weeks,
                persisted_count=saved_count,
                user_id=user_id,
                athlete_id=athlete_id,
            )

            if not persist_result.success or not persist_result.session_ids:
                logger.error(
                    "Execution failed: calendar persistence error",
                    season_start=season_start.isoformat(),
                    season_end=season_end.isoformat(),
                    user_id=user_id,
                    athlete_id=athlete_id,
                )
                _raise_calendar_persistence_failed()

            return generate_season_plan_response(season_start, season_end, saved_count, total_weeks)
        except PersistenceError:
            raise
        except Exception as e:
            logger.exception(f"Error generating season plan: {e}")
            raise RuntimeError(
                "The AI coach failed to generate a valid training plan. Please retry."
            ) from e
    else:
        weeks = (season_end - season_start).days // 7
        return (
            f"**Season Training Plan**\n\n"
            f"Season: {season_start.strftime('%B %d, %Y')} to {season_end.strftime('%B %d, %Y')}\n"
            f"Duration: {weeks} weeks\n\n"
            f"To save this plan to your calendar, please ensure you're logged in and connected to Strava."
        )
