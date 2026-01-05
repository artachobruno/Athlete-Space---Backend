import re
from datetime import datetime, timedelta, timezone

from loguru import logger

from app.coach.tools.session_planner import generate_race_build_sessions, save_planned_sessions


def _extract_race_distance(message_lower: str) -> str | None:
    """Extract race distance from message."""
    if "5k" in message_lower or "5 k" in message_lower:
        return "5K"
    if "10k" in message_lower or "10 k" in message_lower:
        return "10K"
    if "half" in message_lower or "21k" in message_lower or "21.1" in message_lower:
        return "Half Marathon"
    if "marathon" in message_lower or "42k" in message_lower or "42.2" in message_lower or "26.2" in message_lower:
        return "Marathon"
    if "100" in message_lower or "ultra" in message_lower or "ultramarathon" in message_lower:
        return "Ultra"
    return None


def _parse_date_from_message(message_lower: str) -> datetime | None:
    """Parse race date from message using various formats."""
    month_pattern = (
        r"(january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})"
    )
    date_patterns = [
        (r"(\d{4})-(\d{2})-(\d{2})", "iso"),  # YYYY-MM-DD
        (r"(\d{1,2})/(\d{1,2})/(\d{4})", "us"),  # MM/DD/YYYY
        (r"(\d{1,2})-(\d{1,2})-(\d{4})", "us"),  # MM-DD-YYYY
        (month_pattern, "month"),  # Month DD, YYYY
    ]

    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }

    for pattern, fmt in date_patterns:
        match = re.search(pattern, message_lower, re.IGNORECASE)
        if match and len(match.groups()) == 3:
            try:
                if fmt == "iso":
                    return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
                if fmt == "us":
                    return datetime(int(match.group(3)), int(match.group(1)), int(match.group(2)), tzinfo=timezone.utc)
                if fmt == "month":
                    month_name = match.group(1).lower()
                    return datetime(int(match.group(3)), months[month_name], int(match.group(2)), tzinfo=timezone.utc)
            except (ValueError, IndexError, KeyError):
                continue
    return None


def _parse_target_time(message_lower: str) -> str | None:
    """Parse target time from message."""
    time_patterns = [
        r"(\d{1,2}):(\d{2}):(\d{2})",  # HH:MM:SS
        r"(\d{1,2}):(\d{2})",  # HH:MM
        r"(\d+(?:\.\d+)?)\s*h(?:ours?)?",  # X hours
        r"(\d+)\s*h(?:ours?)?\s*(\d+)\s*m(?:in(?:utes?)?)?",  # X hours Y minutes
    ]

    for pattern in time_patterns:
        match = re.search(pattern, message_lower, re.IGNORECASE)
        if match:
            try:
                if ":" in match.group(0):
                    parts = match.group(0).split(":")
                    if len(parts) == 3:
                        return f"{parts[0]}:{parts[1]}:{parts[2]}"
                    if len(parts) == 2:
                        return f"{parts[0]}:{parts[1]}:00"
                else:
                    hours = int(match.group(1)) if match.groups() else 0
                    minutes = int(match.group(2)) if len(match.groups()) > 1 else 0
                    return f"{hours}:{minutes:02d}:00"
            except (ValueError, IndexError):
                continue
    return None


def plan_race_build(message: str, user_id: str | None = None, athlete_id: int | None = None) -> str:
    """Plan a race build and generate training sessions.

    Args:
        message: User message containing race details
        user_id: User ID for saving sessions (optional)
        athlete_id: Athlete ID for saving sessions (optional)

    Returns:
        Response message with plan details or clarification questions
    """
    logger.info(f"Tool plan_race_build called (message_length={len(message)})")
    message_lower = message.lower()

    # Extract race distance, date, and target time
    distance = _extract_race_distance(message_lower)
    race_date = _parse_date_from_message(message_lower)
    target_time = _parse_target_time(message_lower)

    # Check if we have minimum required info
    if not distance or not race_date:
        missing = []
        if not distance:
            missing.append("race distance (e.g., 5K, 10K, half marathon, marathon, ultra)")
        if not race_date:
            missing.append("race date (e.g., 2026-04-15 or April 15, 2026)")

        return (
            f"I'd love to create a personalized race training plan for you! To generate your plan, I need:\n\n"
            f"• **Race distance**: {missing[0] if 'distance' in str(missing) else '✓'}\n"
            f"• **Race date**: {missing[0] if 'date' in str(missing) else '✓'}\n"
            f"• **Target time** (optional): Your goal finish time\n\n"
            f"Once you provide these details, I'll generate a complete training plan with specific sessions "
            f"that will be added to your calendar."
        )

    # Validate race date is in the future
    if race_date < datetime.now(timezone.utc):
        return (
            f"The race date you provided ({race_date.strftime('%Y-%m-%d')}) is in the past. "
            f"Please provide a future race date to generate a training plan."
        )

    # Generate sessions if we have user_id and athlete_id
    if user_id and athlete_id:
        try:
            sessions = generate_race_build_sessions(
                race_date=race_date,
                race_distance=distance,
                target_time=target_time,
            )

            plan_id = f"race_{distance}_{race_date.strftime('%Y%m%d')}"
            saved_count = save_planned_sessions(
                user_id=user_id,
                athlete_id=athlete_id,
                sessions=sessions,
                plan_type="race",
                plan_id=plan_id,
            )

            weeks = len({s.get("week_number", 0) for s in sessions})

            return (
                f"✅ **Race Training Plan Created!**\n\n"
                f"I've generated a {weeks}-week training plan for your **{distance}** race on **{race_date.strftime('%B %d, %Y')}**.\n\n"
                f"**Plan Summary:**\n"
                f"• **{saved_count} training sessions** added to your calendar\n"
                f"• Training starts: {(race_date - timedelta(weeks=weeks)).strftime('%B %d, %Y')}\n"
                f"• Race date: {race_date.strftime('%B %d, %Y')}\n\n"
                f"**Training Structure:**\n"
                f"• Base building phase\n"
                f"• Progressive intensity increases\n"
                f"• Race-specific workouts\n"
                f"• Taper period before race\n\n"
                f"Your planned sessions are now available in your calendar! "
                f"{f'Target time: {target_time}' if target_time else ''}"
            )
        except Exception as e:
            logger.error(f"Error generating race plan: {e}", exc_info=True)
            return (
                f"I've prepared a training plan for your **{distance}** race on **{race_date.strftime('%B %d, %Y')}**, "
                f"but encountered an error saving it. Please try again or contact support."
            )
    else:
        # Return plan details without saving
        weeks = 16 if distance == "Marathon" else (12 if distance in {"5K", "10K"} else 20)
        return (
            f"**{distance} Race Training Plan**\n\n"
            f"Race date: {race_date.strftime('%B %d, %Y')}\n"
            f"Recommended build: {weeks} weeks\n\n"
            f"To save this plan to your calendar, please ensure you're logged in and connected to Strava."
        )
