import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field

from app.coach.services.conversation_progress import (
    clear_progress,
    create_or_update_progress,
    get_conversation_progress,
)
from app.coach.tools.session_planner import generate_race_build_sessions, save_planned_sessions
from app.coach.tools.slot_utils import merge_slots, parse_date_loose
from app.db.models import ConversationProgress

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


def extract_race_information(message: str) -> RaceInformation:
    """Extract race information from user message using simple parsing.

    Args:
        message: User message containing race details

    Returns:
        RaceInformation with extracted fields (may have None values if not found)
    """
    message_lower = message.lower()
    race_info = RaceInformation()

    # Extract distance
    distance_patterns = {
        "5K": ["5k", "5 k", "five k"],
        "10K": ["10k", "10 k", "ten k"],
        "Half Marathon": ["half marathon", "half-marathon", "21k", "21.1k", "13.1"],
        "Marathon": ["marathon", "42k", "42.2k", "26.2"],
        "Ultra": ["ultra", "ultramarathon", "50k", "100k"],
    }
    for distance, patterns in distance_patterns.items():
        if any(pattern in message_lower for pattern in patterns):
            race_info.distance = distance
            break

    # Extract date (simple patterns)
    current_date = datetime.now(timezone.utc)
    month_map = {
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
    date_patterns = [
        (r"(\d{4})-(\d{2})-(\d{2})", lambda m: datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)),
        (r"(\d{1,2})/(\d{1,2})/(\d{4})", lambda m: datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), tzinfo=timezone.utc)),
        (
            r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})",
            lambda m: datetime(int(m.group(3)), month_map[m.group(1).lower()], int(m.group(2)), tzinfo=timezone.utc),
        ),
    ]

    for pattern, parser in date_patterns:
        match = re.search(pattern, message_lower)
        if match:
            try:
                date_obj = parser(match)
                if date_obj >= current_date:
                    race_info.date = date_obj.strftime("%Y-%m-%d")
                    break
            except (ValueError, KeyError, IndexError):
                continue

    # Extract target time (simple patterns)
    time_patterns = [
        (r"(\d{1,2}):(\d{2}):(\d{2})", lambda m: f"{m.group(1)}:{m.group(2)}:{m.group(3)}"),
        (r"(\d{1,2}):(\d{2})", lambda m: f"{m.group(1)}:{m.group(2)}:00"),
        (r"(\d{1,2})h\s*(\d{1,2})m", lambda m: f"{m.group(1)}:{m.group(2)}:00"),
        (r"(\d{1,2})\s*hours?\s*(\d{1,2})\s*minutes?", lambda m: f"{m.group(1)}:{m.group(2)}:00"),
    ]
    for pattern, parser in time_patterns:
        match = re.search(pattern, message_lower)
        if match:
            try:
                race_info.target_time = parser(match)
                break
            except (ValueError, IndexError):
                continue

    logger.info(f"Extracted race information: distance={race_info.distance}, date={race_info.date}, target_time={race_info.target_time}")
    return race_info


def build_clarification_message(distance: str | None, race_date: datetime | None, awaiting_slots: list[str] | None = None) -> str:
    """Build clarification message when required information is missing.

    Args:
        distance: Race distance or None
        race_date: Race date or None
        awaiting_slots: List of specific slots we're waiting for (for slot-scoped messages)

    Returns:
        Clarification message with [CLARIFICATION] prefix
    """
    # If we have awaiting_slots, build slot-scoped message
    if awaiting_slots:
        slot_messages = {
            "race_distance": "race distance (e.g., 5K, 10K, half marathon, marathon, ultra)",
            "race_date": "race date (e.g., April 25, 2026 or 4/25)",
            "target_time": "target finish time (optional)",
        }
        missing_parts = [slot_messages.get(slot, slot) for slot in awaiting_slots if slot in slot_messages]
        if missing_parts:
            clarification_msg = (
                "I just need a bit more information to create your race training plan:\n\n"
                + "\n".join(f"• **{part}**" for part in missing_parts)
                + "\n\n"
                + 'Example: "April 25th" or "on the 25th!"'
            )
            return f"[CLARIFICATION] {clarification_msg}"

    # Fallback to generic message
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


async def create_and_save_plan(
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
        saved_count = await save_planned_sessions(
            user_id=user_id,
            athlete_id=athlete_id,
            sessions=sessions,
            plan_type="race",
            plan_id=plan_id,
        )

        if saved_count > 0:
            logger.info(f"Successfully saved {saved_count} planned sessions")
        else:
            logger.warning("Race plan generated successfully but sessions could not be persisted (service may be temporarily unavailable)")

        weeks = len({s.get("week_number", 0) for s in sessions})
        start_date = (race_date - timedelta(weeks=weeks)).strftime("%B %d, %Y")
        race_date_str = race_date.strftime("%B %d, %Y")
        target_time_str = f"\nTarget time: {target_time}" if target_time else ""

        if saved_count > 0:
            save_status = f"• **{saved_count} training sessions** added to your calendar\n"
            calendar_note = "Your planned sessions are now available in your calendar!"
        else:
            save_status = "• ⚠️ Sessions generated but could not be saved to calendar (service may be temporarily unavailable)\n"
            calendar_note = "The plan is ready, but you may need to retry saving to calendar later."

    except Exception as e:
        logger.error(f"Error generating race plan: {e}", exc_info=True)
        race_date_str = race_date.strftime("%B %d, %Y")
        return (
            f"I've prepared a training plan for your **{distance}** race on **{race_date_str}**, "
            f"but encountered an error generating it. Please try again or contact support."
        )
    else:
        return (
            f"✅ **Race Training Plan Created!**\n\n"
            f"I've generated a {weeks}-week training plan for your **{distance}** "
            f"race on **{race_date_str}**.\n\n"
            f"**Plan Summary:**\n"
            f"{save_status}"
            f"• Training starts: {start_date}\n"
            f"• Race date: {race_date_str}\n\n"
            f"**Training Structure:**\n"
            f"• Base building phase\n"
            f"• Progressive intensity increases\n"
            f"• Race-specific workouts\n"
            f"• Taper period before race\n\n"
            f"{calendar_note}{target_time_str}\n\n"
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


def parse_date_string(date_str: str) -> datetime | None:
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


def resolve_awaited_slots(
    message: str,
    progress: ConversationProgress,
    today: date,
) -> tuple[dict[str, str | datetime | None], list[str]]:
    """Resolve awaited slots from user message.

    Args:
        message: User message
        progress: Conversation progress with current slots and awaiting_slots
        today: Today's date for year inference

    Returns:
        Tuple of (resolved_slots dict, remaining_awaiting_slots list)
    """
    resolved: dict[str, str | datetime | None] = {}
    remaining_awaiting = list(progress.awaiting_slots)

    for slot in progress.awaiting_slots:
        if slot == "race_date":
            # Use loose date parsing for conversational input
            parsed_date = parse_date_loose(
                message,
                today=today,
                known_slots=progress.slots,
            )
            if parsed_date:
                # Convert date to datetime for storage
                resolved["race_date"] = datetime.combine(parsed_date, datetime.min.time()).replace(tzinfo=timezone.utc)
                remaining_awaiting.remove(slot)
                logger.info(
                    "Resolved awaited slot",
                    slot="race_date",
                    value=parsed_date,
                    conversation_id=progress.conversation_id,
                )
        elif slot == "race_distance":
            # Extract distance from message
            race_info = extract_race_information(message)
            if race_info.distance:
                resolved["race_distance"] = race_info.distance
                remaining_awaiting.remove(slot)
                logger.info(
                    "Resolved awaited slot",
                    slot="race_distance",
                    value=race_info.distance,
                    conversation_id=progress.conversation_id,
                )
        elif slot == "target_time":
            # Extract target time from message
            race_info = extract_race_information(message)
            if race_info.target_time:
                resolved["target_time"] = race_info.target_time
                remaining_awaiting.remove(slot)
                logger.info(
                    "Resolved awaited slot",
                    slot="target_time",
                    value=race_info.target_time,
                    conversation_id=progress.conversation_id,
                )

    return resolved, remaining_awaiting


async def plan_race_build(
    message: str,
    user_id: str | None = None,
    athlete_id: int | None = None,
    conversation_id: str | None = None,
) -> str:
    """Plan a race build and generate training sessions.

    Uses stateful slot extraction with cumulative accumulation and awaited slot resolution.

    Args:
        message: User message containing race details
        user_id: User ID for saving sessions (optional)
        athlete_id: Athlete ID for saving sessions (optional)
        conversation_id: Conversation ID for stateful slot tracking (optional but recommended)

    Returns:
        Response message with plan details or clarification questions
    """
    logger.info(
        "Tool plan_race_build called",
        message_length=len(message),
        conversation_id=conversation_id,
    )

    # Get or create conversation progress
    if conversation_id:
        progress = get_conversation_progress(conversation_id)
        if progress is None:
            # Create new progress for this intent
            progress = create_or_update_progress(
                conversation_id=conversation_id,
                intent="race_plan",
                slots={},
                awaiting_slots=[],
            )
        # Update intent if it changed
        elif progress.intent != "race_plan":
            progress = create_or_update_progress(
                conversation_id=conversation_id,
                intent="race_plan",
                slots=progress.slots,
                awaiting_slots=progress.awaiting_slots,
            )
    else:
        # No conversation_id - use stateless mode (backward compatibility)
        progress = None

    # PART B: Bypass intent detection if awaiting slots exist
    if progress and progress.awaiting_slots:
        logger.debug(
            "Resolving awaited slots",
            awaiting_slots=progress.awaiting_slots,
            conversation_id=conversation_id,
        )
        today = datetime.now(timezone.utc).date()
        resolved_slots, remaining_awaiting = resolve_awaited_slots(message, progress, today)

        # Merge resolved slots into existing slots
        old_slots = progress.slots.copy()
        progress.slots = merge_slots(progress.slots, resolved_slots)
        progress.awaiting_slots = remaining_awaiting

        logger.debug(
            "Merged slots after awaited resolution",
            before=old_slots,
            after=progress.slots,
            conversation_id=conversation_id,
        )

        # Update progress
        if conversation_id:
            progress = create_or_update_progress(
                conversation_id=conversation_id,
                intent="race_plan",
                slots=progress.slots,
                awaiting_slots=progress.awaiting_slots,
            )

        # If still awaiting slots, ask for them
        if progress.awaiting_slots:
            logger.info(
                "Still awaiting slots after resolution",
                awaiting_slots=progress.awaiting_slots,
                conversation_id=conversation_id,
            )
            distance = progress.slots.get("race_distance")
            race_date_str = progress.slots.get("race_date")
            race_date = race_date_str if isinstance(race_date_str, datetime) else None
            return build_clarification_message(distance, race_date, progress.awaiting_slots)

        # All slots resolved - continue to tool execution
        logger.info(
            "All awaited slots resolved, proceeding to tool execution",
            conversation_id=conversation_id,
        )

    # Extract new slots from current message
    race_info = extract_race_information(message)
    new_slots: dict[str, str | datetime | None] = {}
    if race_info.distance:
        new_slots["race_distance"] = race_info.distance
    if race_info.date:
        parsed_date = parse_date_string(race_info.date)
        if parsed_date:
            new_slots["race_date"] = parsed_date
    if race_info.target_time:
        new_slots["target_time"] = race_info.target_time

    logger.debug("Extracted slots", slots=new_slots, conversation_id=conversation_id)

    # Merge with existing slots (if we have progress)
    if progress:
        old_slots = progress.slots.copy()
        merged_slots = merge_slots(progress.slots, new_slots)
        progress.slots = merged_slots
        logger.debug(
            "Merged slots",
            before=old_slots,
            after=merged_slots,
            conversation_id=conversation_id,
        )
        current_slots = merged_slots
    else:
        # No progress - use new slots directly
        current_slots = new_slots

    # Determine what slots we still need with proper type extraction
    distance_raw = current_slots.get("race_distance")
    race_date_raw = current_slots.get("race_date")
    target_time_raw = current_slots.get("target_time")

    # Extract and validate distance (must be str or None)
    distance: str | None = None
    if isinstance(distance_raw, str):
        distance = distance_raw

    # Extract and validate race_date (must be datetime or None)
    race_date: datetime | None = None
    if isinstance(race_date_raw, datetime):
        race_date = race_date_raw
    elif isinstance(race_date_raw, str):
        race_date = parse_date_string(race_date_raw)

    # Extract and validate target_time (must be str or None)
    target_time: str | None = None
    if isinstance(target_time_raw, str):
        target_time = target_time_raw

    # Determine awaiting slots
    awaiting_slots: list[str] = []
    if not distance:
        awaiting_slots.append("race_distance")
    if not race_date:
        awaiting_slots.append("race_date")

    # Update progress with current state
    if conversation_id:
        progress = create_or_update_progress(
            conversation_id=conversation_id,
            intent="race_plan",
            slots=current_slots,
            awaiting_slots=awaiting_slots,
        )

    # If we're missing required slots, ask for them
    if awaiting_slots:
        logger.info(
            "Missing required slots, asking for clarification",
            awaiting_slots=awaiting_slots,
            conversation_id=conversation_id,
        )
        return build_clarification_message(distance, race_date, awaiting_slots)

    # Validate race date is in the future
    if race_date and race_date < datetime.now(timezone.utc):
        return (
            f"The race date you provided ({race_date.strftime('%Y-%m-%d')}) is in the past. "
            f"Please provide a future race date to generate a training plan."
        )

    # Type narrowing: distance and race_date are guaranteed to be non-None here
    if not isinstance(distance, str):
        return build_clarification_message(None, None, ["race_distance"])
    if not isinstance(race_date, datetime):
        return build_clarification_message(None, None, ["race_date"])

    # All required slots filled - execute tool
    logger.info(
        "All required slots filled, executing tool",
        distance=distance,
        race_date=race_date,
        target_time=target_time,
        conversation_id=conversation_id,
    )

    # Clear progress after successful execution
    if conversation_id:
        clear_progress(conversation_id)
        logger.info("Cleared conversation progress after successful execution", conversation_id=conversation_id)

    # Generate sessions if we have user_id and athlete_id
    if user_id and athlete_id:
        logger.info(
            "Creating and saving race plan",
            user_id=user_id,
            athlete_id=athlete_id,
            distance=distance,
            date=race_date,
        )
        return await create_and_save_plan(race_date, distance, target_time, user_id, athlete_id)

    # Return plan details without saving
    logger.warning(
        "Missing user_id or athlete_id - returning preview plan",
        user_id=user_id,
        athlete_id=athlete_id,
    )
    return _build_preview_plan(distance, race_date)
