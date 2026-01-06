from datetime import datetime, timedelta, timezone
from typing import Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import BaseModel, Field, SecretStr

from app.coach.tools.session_planner import generate_race_build_sessions, save_planned_sessions
from app.config.settings import settings

# Simple cache to prevent repeated calls with same input (cleared periodically)
_recent_calls: dict[str, datetime] = {}


class RaceInformation(BaseModel):
    """Structured race information extracted from user message."""

    distance: Literal["5K", "10K", "Half Marathon", "Marathon", "Ultra"] | None = Field(
        default=None,
        description=("Race distance. Extract from terms like '5k', '10k', 'half marathon', 'marathon', 'ultra', '42k', '26.2 miles', etc."),
    )
    date: str | None = Field(
        default=None,
        description=(
            "Race date in ISO format (YYYY-MM-DD). Extract from various date formats. "
            "If year is missing, assume current or next year. Must be a future date."
        ),
    )
    target_time: str | None = Field(
        default=None,
        description=(
            "Target finish time in HH:MM:SS format (e.g., '2:30:00' for 2 hours 30 minutes). "
            "Extract from time expressions like '2:25', '2h30m', '2 hours 25 minutes', etc."
        ),
    )


def _extract_race_information(message: str) -> RaceInformation:
    """Extract race information from user message using LLM.

    Args:
        message: User message containing race details

    Returns:
        RaceInformation with extracted fields (may have None values if not found)
    """
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot extract race information with LLM")
        return RaceInformation()

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.0,
            api_key=SecretStr(settings.openai_api_key),
        )

        system_prompt = (
            "You are a race information extractor. Extract race details from user messages.\n\n"
            "Extract:\n"
            "- Race distance: 5K, 10K, Half Marathon, Marathon, or Ultra\n"
            "- Race date: Convert to YYYY-MM-DD format. "
            "Handle formats like: MM/DD, MM/DD/YYYY, MM-DD, 'April 5', 'April 5 2026', etc. "
            "For MM/DD format, interpret as month/day (e.g., '04/05' = April 5). "
            "If year is missing, assume current year ({current_date}) or next year if the date has already passed.\n"
            '- Target time: Convert to HH:MM:SS format (e.g., "2:25" becomes "2:25:00", '
            '"2h30m" becomes "2:30:00", "2 hours 25 minutes" becomes "2:25:00")\n\n'
            "If information is not present or unclear, set the field to null.\n\n"
            "Current date: {current_date}"
        )
        # LangChain template variable, not an f-string - suppress RUF027 false positive
        human_msg = "{message}"  # noqa: RUF027
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", human_msg),
        ])

        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        chain = prompt | llm.with_structured_output(RaceInformation)
        result = chain.invoke({"message": message, "current_date": current_date})
        race_info = RaceInformation.model_validate(result) if isinstance(result, dict) else result

        logger.info(
            f"Extracted race information: distance={race_info.distance}, date={race_info.date}, target_time={race_info.target_time}"
        )
    except Exception as e:
        logger.error(f"Error extracting race information with LLM: {e}", exc_info=True)
        race_info = RaceInformation()
    return race_info


def _build_clarification_message(distance: str | None, race_date: datetime | None) -> str:
    """Build clarification message when required information is missing.

    Args:
        distance: Race distance or None
        race_date: Race date or None

    Returns:
        Clarification message with [CLARIFICATION] prefix
    """
    missing = []
    if not distance:
        missing.append("race distance (e.g., 5K, 10K, half marathon, marathon, ultra)")
    if not race_date:
        missing.append("race date (e.g., 2026-04-15 or April 15, 2026)")

    clarification_msg = (
        "I'd love to create a personalized race training plan for you! "
        "To generate your plan, I need:\n\n"
        f"• **Race distance**: {missing[0] if missing and 'distance' in missing[0] else '✓'}\n"
        f"• **Race date**: {missing[0] if missing and 'date' in missing[0] else '✓'}\n"
        f"• **Target time** (optional): Your goal finish time\n\n"
        f"**Please provide both the race distance and date in your message**, and I'll generate "
        f"a complete training plan with specific sessions that will be added to your calendar.\n\n"
        f'Example: "I want to train for a marathon on April 15, 2026"'
    )
    return f"[CLARIFICATION] {clarification_msg}"


def _create_and_save_plan(
    race_date: datetime,
    distance: str,
    target_time: str | None,
    user_id: str,
    athlete_id: int,
) -> str:
    """Create and save race training plan.

    Args:
        race_date: Race date
        distance: Race distance
        target_time: Target finish time or None
        user_id: User ID
        athlete_id: Athlete ID

    Returns:
        Success message or error message
    """
    try:
        logger.info(f"Generating race build sessions for {distance} on {race_date}")
        sessions = generate_race_build_sessions(
            race_date=race_date,
            race_distance=distance,
            target_time=target_time,
        )
        logger.info(f"Generated {len(sessions)} sessions for race plan")

        plan_id = f"race_{distance}_{race_date.strftime('%Y%m%d')}"
        logger.info(f"Saving {len(sessions)} planned sessions with plan_id={plan_id}")
        saved_count = save_planned_sessions(
            user_id=user_id,
            athlete_id=athlete_id,
            sessions=sessions,
            plan_type="race",
            plan_id=plan_id,
        )
        logger.info(f"Successfully saved {saved_count} planned sessions")

        weeks = len({s.get("week_number", 0) for s in sessions})
        start_date = (race_date - timedelta(weeks=weeks)).strftime("%B %d, %Y")
        race_date_str = race_date.strftime("%B %d, %Y")
        target_time_str = f"\nTarget time: {target_time}" if target_time else ""
    except Exception as e:
        logger.error(f"Error generating race plan: {e}", exc_info=True)
        race_date_str = race_date.strftime("%B %d, %Y")
        return (
            f"I've prepared a training plan for your **{distance}** race on **{race_date_str}**, "
            f"but encountered an error saving it. Please try again or contact support."
        )
    else:
        return (
            f"✅ **Race Training Plan Created!**\n\n"
            f"I've generated a {weeks}-week training plan for your **{distance}** "
            f"race on **{race_date_str}**.\n\n"
            f"**Plan Summary:**\n"
            f"• **{saved_count} training sessions** added to your calendar\n"
            f"• Training starts: {start_date}\n"
            f"• Race date: {race_date_str}\n\n"
            f"**Training Structure:**\n"
            f"• Base building phase\n"
            f"• Progressive intensity increases\n"
            f"• Race-specific workouts\n"
            f"• Taper period before race\n\n"
            f"Your planned sessions are now available in your calendar!{target_time_str}\n\n"
            f"**The plan is complete and ready to use. No further action needed.**"
        )


def _build_preview_plan(distance: str, race_date: datetime) -> str:
    """Build preview plan message when user is not authenticated.

    Args:
        distance: Race distance
        race_date: Race date

    Returns:
        Preview plan message
    """
    weeks = 16 if distance == "Marathon" else (12 if distance in {"5K", "10K"} else 20)
    race_date_str = race_date.strftime("%B %d, %Y")
    return (
        f"**{distance} Race Training Plan**\n\n"
        f"Race date: {race_date_str}\n"
        f"Recommended build: {weeks} weeks\n\n"
        f"To save this plan to your calendar, please ensure you're logged in and connected to Strava."
    )


def _parse_date_string(date_str: str) -> datetime | None:
    """Parse date string in ISO format to datetime.

    Args:
        date_str: Date string in YYYY-MM-DD format

    Returns:
        datetime object or None if parsing fails
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
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
    message_lower = message.lower().strip()

    # Create a simple hash of the message for duplicate detection
    message_hash = str(hash(message_lower[:100]))  # Use first 100 chars
    now = datetime.now(timezone.utc)

    # Check if we've been called recently with similar input (within last 10 seconds)
    if message_hash in _recent_calls:
        last_time = _recent_calls[message_hash]
        if (now - last_time).total_seconds() < 10:
            logger.warning("Duplicate tool call detected within 10 seconds, blocking repeat call")
            return (
                "I've already provided information about race planning. "
                "**Please do not call this tool again with the same input.**\n\n"
                "To create a specific training plan, provide both the race distance and date in your message:\n\n"
                "• **Race distance** (e.g., 5K, 10K, half marathon, marathon, ultra)\n"
                "• **Race date** (e.g., 2026-04-15 or April 15, 2026)\n\n"
                'Example: "I want to train for a marathon on April 15, 2026"'
            )

    # Update cache
    _recent_calls[message_hash] = now
    # Clean old entries (older than 30 seconds) to prevent memory growth
    cutoff = now - timedelta(seconds=30)
    # Filter and update cache in place to avoid type checker issues
    keys_to_remove = [k for k, v in _recent_calls.items() if v <= cutoff]
    for key in keys_to_remove:
        del _recent_calls[key]

    # Extract race information using LLM
    race_info = _extract_race_information(message)
    distance = race_info.distance
    race_date = _parse_date_string(race_info.date) if race_info.date else None
    target_time = race_info.target_time

    # Check if we have minimum required info
    if not distance or not race_date:
        return _build_clarification_message(distance, race_date)

    # Validate race date is in the future
    if race_date < datetime.now(timezone.utc):
        return (
            f"The race date you provided ({race_date.strftime('%Y-%m-%d')}) is in the past. "
            f"Please provide a future race date to generate a training plan."
        )

    # Generate sessions if we have user_id and athlete_id
    if user_id and athlete_id:
        logger.info(f"Creating and saving race plan: user_id={user_id}, athlete_id={athlete_id}, distance={distance}, date={race_date}")
        return _create_and_save_plan(race_date, distance, target_time, user_id, athlete_id)

    # Return plan details without saving
    logger.warning(f"Missing user_id or athlete_id - returning preview plan. user_id={user_id}, athlete_id={athlete_id}")
    return _build_preview_plan(distance, race_date)
