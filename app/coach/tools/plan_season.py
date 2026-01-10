from datetime import datetime, timedelta, timezone

from loguru import logger

from app.coach.tools.session_planner import generate_season_sessions, save_planned_sessions
from app.coach.utils.date_extraction import extract_dates_from_text

# Cache to prevent duplicate calls within a short time window
_recent_calls: dict[str, datetime] = {}


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
    """Generate success response for season plan creation."""
    if saved_count > 0:
        save_status = f"• **{saved_count} training sessions** added to your calendar\n"
        calendar_note = (
            "Your planned sessions are now available in your calendar! You can view them in the calendar view and track your progress."
        )
    else:
        save_status = "• ⚠️ Sessions generated but could not be saved to calendar (service may be temporarily unavailable)\n"
        calendar_note = "The plan is ready, but you may need to retry saving to calendar later."

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
    logger.info(f"Tool plan_season called (message_length={len(message)})")
    message_lower = message.lower().strip() if message else ""

    # Create a simple hash of the message for duplicate detection
    message_hash = str(hash(message_lower[:100]))  # Use first 100 chars
    now = datetime.now(timezone.utc)

    # Check if we've been called recently with similar input (within last 10 seconds)
    if message_hash in _recent_calls:
        last_time = _recent_calls[message_hash]
        if (now - last_time).total_seconds() < 10:
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
    for key in keys_to_remove:
        del _recent_calls[key]

    # Extract season dates
    season_start, season_end = parse_season_dates(message)

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

    # Generate sessions if we have user_id and athlete_id
    if user_id and athlete_id:
        try:
            sessions = generate_season_sessions(
                season_start=season_start,
                season_end=season_end,
                target_races=None,  # Could be enhanced to parse races from message
            )

            plan_id = f"season_{season_start.strftime('%Y%m%d')}_{season_end.strftime('%Y%m%d')}"
            saved_count = await save_planned_sessions(
                user_id=user_id,
                athlete_id=athlete_id,
                sessions=sessions,
                plan_type="season",
                plan_id=plan_id,
            )

            if saved_count > 0:
                logger.info(f"Successfully saved {saved_count} planned sessions for season plan")
            else:
                logger.warning(
                    "Season plan generated successfully but sessions could not be persisted (service may be temporarily unavailable)"
                )

            weeks = (season_end - season_start).days // 7
            return generate_season_plan_response(season_start, season_end, saved_count, weeks)
        except Exception as e:
            logger.error(f"Error generating season plan: {e}", exc_info=True)
            return (
                f"I've prepared a season training plan from **{season_start.strftime('%B %d, %Y')}** "
                f"to **{season_end.strftime('%B %d, %Y')}**, but encountered an error generating it. "
                f"Please try again or contact support."
            )
    else:
        weeks = (season_end - season_start).days // 7
        return (
            f"**Season Training Plan**\n\n"
            f"Season: {season_start.strftime('%B %d, %Y')} to {season_end.strftime('%B %d, %Y')}\n"
            f"Duration: {weeks} weeks\n\n"
            f"To save this plan to your calendar, please ensure you're logged in and connected to Strava."
        )
