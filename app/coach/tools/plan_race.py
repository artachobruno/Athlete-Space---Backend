from datetime import date, datetime, timedelta, timezone
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from app.coach.schemas.training_plan_schemas import TrainingPlan
from app.coach.services.conversation_progress import (
    clear_progress,
    create_or_update_progress,
    get_conversation_progress,
)
from app.coach.tools.session_planner import save_planned_sessions
from app.coach.tools.slot_utils import merge_slots, parse_date_loose
from app.coach.utils.llm_client import CoachLLMClient
from app.db.models import ConversationProgress
from app.services.llm.model import get_model

# Simple cache to prevent repeated calls with same input (cleared periodically)
_recent_calls: dict[str, datetime] = {}

# Use cheap model for extraction
EXTRACTION_MODEL = "gpt-4o-mini"


def _raise_no_sessions_error() -> None:
    """Raise error when training plan has no sessions."""
    raise RuntimeError("Training plan generated with no sessions")


def _raise_invalid_date_type_error(first_date: date | datetime) -> None:
    """Raise error for invalid date type in training plan."""
    raise TypeError(f"Invalid date type in training plan: {type(first_date)}")


def _raise_save_failed_error() -> None:
    """Raise error when plan save fails."""
    raise RuntimeError(
        "Failed to save race training plan: no sessions were saved. "
        "Plan generation must be retried."
    )


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


class TrainingGoalInformation(BaseModel):
    """Structured training goal information extracted from user message with conversation context."""

    race_name: str | None = Field(
        default=None,
        description="Official or informal race name if mentioned",
    )
    race_distance: Literal["5K", "10K", "Half Marathon", "Marathon", "Ultra"] | None = Field(
        default=None,
        description="Race distance. Extract from terms like '5k', '10k', 'half marathon', 'marathon', 'ultra', '42k', '26.2 miles', etc.",
    )
    race_date: str | None = Field(
        default=None,
        description=(
            "Race date in YYYY-MM-DD format. Extract from various date formats. "
            "If year is missing, infer current or next year based on today's date."
        ),
    )
    training_start_date: str | None = Field(
        default=None,
        description="Training start date in YYYY-MM-DD format if mentioned",
    )
    training_duration_weeks: int | None = Field(
        default=None,
        description="Training duration in weeks if mentioned",
    )
    target_finish_time: str | None = Field(
        default=None,
        description=(
            "Target finish time in HH:MM:SS format (e.g., '03:00:00' for 3 hours). "
            "Extract from time expressions like 'sub 3', 'under 2 hours', '2:45 marathon', etc."
        ),
    )
    goal_type: Literal["finish", "time", "performance", "completion"] | None = Field(
        default=None,
        description=(
            "Goal type: 'time' for time goals, 'finish' for finishing goals, "
            "'performance' for PR/qualify/podium, 'completion' for completion emphasis"
        ),
    )
    notes: str | None = Field(
        default=None,
        description="Short free-text clarification if useful",
    )


def extract_race_information(message: str) -> RaceInformation:
    """Extract race information from user message using LLM-based extraction.

    Args:
        message: User message containing race details

    Returns:
        RaceInformation with extracted fields (may have None values if not found)
    """
    logger.info(f"Extracting race information from message: {message[:100]}...")

    today = datetime.now(timezone.utc).date()
    today_str = today.strftime("%Y-%m-%d")
    current_year = today.year

    system_prompt = f"""You are a race information extraction assistant. Extract structured race information from user messages.

Today's date is {today_str} (year: {current_year}).

Your task:
- Extract race distance (must be one of: "5K", "10K", "Half Marathon", "Marathon", "Ultra")
- Extract race date in YYYY-MM-DD format (if year is missing, infer current or next year based on today's date)
- Extract target finish time in HH:MM:SS format if mentioned

Rules:
- Only extract information that is explicitly mentioned or clearly implied
- If information is not available, set field to null
- Be conservative - don't guess or infer
- Dates should be in YYYY-MM-DD format
- If only month/day is mentioned (e.g., "April 25th", "on the 25th"), infer the year:
  * If the date (with current year) hasn't passed yet, use current year
  * If the date (with current year) has passed, use next year
- Target times should be normalized to HH:MM:SS format (e.g., "2:30:00" for 2 hours 30 minutes)
- Distance must match exactly: "5K", "10K", "Half Marathon", "Marathon", or "Ultra"

Example inputs (assuming today is {today_str}):
- "on the 25th!" -> date: infer month from context or use current month, year based on whether date has passed
- "I'm training for a marathon in April 25th" -> distance: "Marathon", date: "{current_year}-04-25" or "{current_year + 1}-04-25"
- "marathon on April 15, 2026" -> distance: "Marathon", date: "2026-04-15"
- "half marathon under 2 hours" -> distance: "Half Marathon", target_time: "2:00:00"
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=RaceInformation,
    )

    try:
        result = agent.run_sync(f"Extract race information from this message: {message}")
        race_info = result.output

        logger.info(
            f"Extraction successful: distance={race_info.distance}, date={race_info.date}, target_time={race_info.target_time}",
        )
    except Exception as e:
        logger.error(f"Failed to extract race information: {e}", exc_info=True)
        # Return empty information on failure (non-blocking)
        race_info = RaceInformation()

    return race_info


async def extract_training_goal(
    latest_user_message: str,
    conversation_context: dict[str, str | None],
    awaiting_slots: list[str],
    today: date,
) -> TrainingGoalInformation:
    """Extract training goal information from user message with conversation context.

    This function uses conversation context to resolve partial or follow-up answers
    and prioritizes resolving awaited slots before general extraction.

    Args:
        latest_user_message: The user's latest message
        conversation_context: Dictionary with known facts from previous turns:
            - known_race_name: str | None
            - known_race_distance: str | None
            - known_race_date: str | None (YYYY-MM-DD)
            - known_race_month: str | None
            - known_target_time: str | None (HH:MM:SS)
            - known_goal_type: str | None
        awaiting_slots: List of slot names the system is currently awaiting
        today: Today's date for year inference

    Returns:
        TrainingGoalInformation with extracted fields (may have None values if not found)
    """
    logger.info(
        f"Extracting training goal from message: {latest_user_message[:100]}...",
        awaiting_slots=awaiting_slots,
        has_context=bool(conversation_context),
    )

    today_str = today.strftime("%Y-%m-%d")
    current_year = today.year

    # Build context string for prompt
    context_parts = []
    if conversation_context.get("known_race_name"):
        context_parts.append(f"Race name: {conversation_context['known_race_name']}")
    if conversation_context.get("known_race_distance"):
        context_parts.append(f"Race distance: {conversation_context['known_race_distance']}")
    if conversation_context.get("known_race_date"):
        context_parts.append(f"Race date: {conversation_context['known_race_date']}")
    if conversation_context.get("known_race_month"):
        context_parts.append(f"Race month: {conversation_context['known_race_month']}")
    if conversation_context.get("known_target_time"):
        context_parts.append(f"Target time: {conversation_context['known_target_time']}")
    if conversation_context.get("known_goal_type"):
        context_parts.append(f"Goal type: {conversation_context['known_goal_type']}")

    context_str = "\n".join(context_parts) if context_parts else "No previous context."

    awaiting_str = ", ".join(awaiting_slots) if awaiting_slots else "None"

    system_prompt = f"""You are a structured information extraction assistant for endurance training and race planning.

Today's date is {today_str} (year: {current_year}).

You will receive:
1. The user's latest message
2. Conversation context containing previously known facts
3. A list of slots that the system is currently awaiting

Your job is to extract or resolve structured training and race information.

━━━━━━━━━━━━━━━━━━━
FIELDS TO EXTRACT
━━━━━━━━━━━━━━━━━━━

Return a JSON object with the following fields:

- race_name: Official or informal race name if mentioned
- race_distance: One of ["5K", "10K", "Half Marathon", "Marathon", "Ultra"]
- race_date: YYYY-MM-DD
- training_start_date: YYYY-MM-DD
- training_duration_weeks: integer
- target_finish_time: HH:MM:SS
- goal_type: one of ["finish", "time", "performance", "completion"]
- notes: short free-text clarification if useful

━━━━━━━━━━━━━━━━━━━
CORE RULES (STRICT)
━━━━━━━━━━━━━━━━━━━

1. Use conversation_context to resolve partial or follow-up answers.
2. If awaiting_slots is not empty, prioritize resolving those slots.
3. Do NOT ask clarifying questions.
4. Do NOT invent or guess missing information.
5. Only infer a year for dates when month/day are given.

━━━━━━━━━━━━━━━━━━━
DATE RESOLUTION RULES
━━━━━━━━━━━━━━━━━━━

If a date is incomplete:

- "on the 25th":
  • Use known month from conversation_context if available
  • Otherwise, leave race_date as null

- Month/day without year:
  • If date (with current year) is in the future → use current year
  • If date has passed → use next year

- Never infer past dates
- Normalize all dates to YYYY-MM-DD

━━━━━━━━━━━━━━━━━━━
TIME NORMALIZATION
━━━━━━━━━━━━━━━━━━━

Normalize all times to HH:MM:SS:
- "sub 3" → 03:00:00
- "under 2 hours" → 02:00:00
- "2:45 marathon" → 02:45:00

━━━━━━━━━━━━━━━━━━━
GOAL TYPE INFERENCE
━━━━━━━━━━━━━━━━━━━

- Mentions of time goals → goal_type = "time"
- Mentions of finishing → goal_type = "finish"
- Performance language (PR, qualify, podium) → "performance"
- Completion emphasis → "completion"

━━━━━━━━━━━━━━━━━━━
OUTPUT RULES
━━━━━━━━━━━━━━━━━━━

- Return ONLY valid JSON
- Use null for unknown fields
- Do not include explanations
- Do not include extra keys
- Deterministic output for identical inputs

━━━━━━━━━━━━━━━━━━━
CONVERSATION CONTEXT
━━━━━━━━━━━━━━━━━━━

{context_str}

━━━━━━━━━━━━━━━━━━━
AWAITING SLOTS
━━━━━━━━━━━━━━━━━━━

{awaiting_str}
"""

    model = get_model("openai", EXTRACTION_MODEL)
    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        output_type=TrainingGoalInformation,
    )

    try:
        logger.debug(
            "plan_race: Extracting training goal via LLM",
            message_length=len(latest_user_message),
            message_preview=latest_user_message[:100],
            has_context=bool(conversation_context),
            awaiting_slots=awaiting_slots,
        )
        user_prompt = f"Extract training goal information from this message: {latest_user_message}"
        logger.debug(
            "plan_race: Calling LLM agent for goal extraction",
            prompt_length=len(user_prompt),
        )
        result = await agent.run(user_prompt)
        goal_info = result.output
        logger.debug(
            "plan_race: Goal extraction completed",
            race_distance=goal_info.race_distance,
            race_date=goal_info.race_date,
            target_finish_time=goal_info.target_finish_time,
            goal_type=goal_info.goal_type,
            has_race_name=bool(goal_info.race_name),
        )

        logger.info(
            f"Extraction successful: race_distance={goal_info.race_distance}, "
            f"race_date={goal_info.race_date}, target_finish_time={goal_info.target_finish_time}, "
            f"goal_type={goal_info.goal_type}",
        )
    except Exception as e:
        logger.debug(
            "plan_race: Goal extraction failed",
            error_type=type(e).__name__,
            error_message=str(e),
        )
        logger.error(f"Failed to extract training goal: {e}", exc_info=True)
        # Return empty information on failure (non-blocking)
        goal_info = TrainingGoalInformation()

    return goal_info


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
) -> tuple[str, int]:
    """Create and save race training plan.

    Args:
        race_date: Race date
        distance: Race distance
        target_time: Target finish time or None
        user_id: User ID
        athlete_id: Athlete ID

    Returns:
        Tuple of (success message, saved_count)
    """
    try:
        logger.info(
            "Starting race plan generation via LLM",
            distance=distance,
            race_date=race_date.isoformat(),
            target_time=target_time,
            user_id=user_id,
            athlete_id=athlete_id,
        )
        logger.debug(
            "plan_race: Starting create_and_save_plan",
            distance=distance,
            race_date=race_date.isoformat(),
            target_time=target_time,
            user_id=user_id,
            athlete_id=athlete_id,
        )

        # Generate plan via LLM - single source of truth
        logger.debug("plan_race: Creating LLM client")
        llm_client = CoachLLMClient()
        goal_context = {
            "plan_type": "race",
            "race_distance": distance,
            "race_date": race_date.isoformat(),
            "target_time": target_time,
        }
        user_context = {
            "user_id": user_id,
            "athlete_id": athlete_id,
        }
        athlete_context = {}  # TODO: Fill with actual athlete context
        calendar_constraints = {}  # TODO: Fill with actual calendar constraints

        logger.debug(
            "plan_race: Preparing context for LLM generation",
            goal_context_keys=list(goal_context.keys()),
            user_context_keys=list(user_context.keys()),
            athlete_context_keys=list(athlete_context.keys()),
            calendar_constraints_keys=list(calendar_constraints.keys()),
        )
        logger.debug(
            "plan_race: Calling generate_training_plan_via_llm",
            goal_context=goal_context,
            user_context=user_context,
        )
        training_plan = await llm_client.generate_training_plan_via_llm(
            user_context=user_context,
            athlete_context=athlete_context,
            goal_context=goal_context,
            calendar_constraints=calendar_constraints,
        )
        logger.debug(
            "plan_race: Training plan generated via LLM",
            plan_type=training_plan.plan_type,
            session_count=len(training_plan.sessions),
            has_rationale=bool(training_plan.rationale),
            assumptions_count=len(training_plan.assumptions),
        )

        # Convert TrainingPlan to session dictionaries
        # Phase 6: Persist exactly what LLM returns - only minimal field name mapping for DB compatibility
        # sport -> type is a field name mapping, not a logic mutation
        logger.debug(
            "plan_race: Converting TrainingPlan to session dictionaries",
            total_sessions=len(training_plan.sessions),
        )
        sessions = []
        for idx, session in enumerate(training_plan.sessions):
            logger.debug(
                "plan_race: Converting session to dict",
                index=idx,
                session_date=session.date.isoformat(),
                session_sport=session.sport,
                session_title=session.title,
                session_intensity=session.intensity,
                session_week_number=session.week_number,
            )
            # Map sport field to type field (database schema expects "type" not "sport")
            # Preserve all other fields exactly as LLM provides
            session_dict: dict = {
                "date": session.date,  # Exactly as LLM - timezone preserved
                "type": session.sport.capitalize() if session.sport != "rest" else "Rest",  # Field name mapping only
                "title": session.title,  # Exactly as LLM
                "description": session.description,  # Exactly as LLM
                "duration_minutes": session.duration_minutes,  # Exactly as LLM
                "distance_km": session.distance_km,  # Exactly as LLM
                "intensity": session.intensity,  # Exactly as LLM
                "notes": session.purpose,  # Exactly as LLM
                "week_number": session.week_number,  # Exactly as LLM
            }
            sessions.append(session_dict)
        logger.debug(
            "plan_race: Sessions converted",
            total_sessions=len(sessions),
            first_session_date=sessions[0]["date"].isoformat() if sessions else None,
            last_session_date=sessions[-1]["date"].isoformat() if sessions else None,
        )

        # Calculate weeks and start date from training plan sessions
        if not sessions:
            _raise_no_sessions_error()

        dates = sorted([s["date"].date() if isinstance(s["date"], datetime) else s["date"] for s in sessions])
        first_date = dates[0]
        if isinstance(first_date, date):
            start_date_dt = datetime.combine(first_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        elif isinstance(first_date, datetime):
            start_date_dt = first_date
        else:
            _raise_invalid_date_type_error(first_date)
        weeks = len({s.get("week_number", 0) for s in sessions})
        start_date = start_date_dt.strftime("%B %d, %Y")
        race_date_str = race_date.strftime("%B %d, %Y")

        # Calculate session statistics
        session_types: dict[str, int] = {}
        sessions_by_week: dict[int, int] = {}
        for session in sessions:
            session_type = session.get("type", "Unknown")
            session_types[session_type] = session_types.get(session_type, 0) + 1
            week_num = session.get("week_number", 0)
            sessions_by_week[week_num] = sessions_by_week.get(week_num, 0) + 1

        plan_id = f"race_{distance}_{race_date.strftime('%Y%m%d')}"

        logger.info(
            "Race plan generated successfully",
            plan_id=plan_id,
            total_sessions=len(sessions),
            total_weeks=weeks,
            start_date=start_date_dt.isoformat(),
            race_date=race_date.isoformat(),
            session_types=session_types,
            sessions_per_week=sessions_by_week,
            user_id=user_id,
            athlete_id=athlete_id,
        )

        logger.info(
            "Attempting to save planned sessions",
            plan_id=plan_id,
            session_count=len(sessions),
            user_id=user_id,
            athlete_id=athlete_id,
        )
        logger.debug(
            "plan_race: Calling save_planned_sessions via MCP",
            plan_id=plan_id,
            session_count=len(sessions),
            plan_type="race",
            user_id=user_id,
            athlete_id=athlete_id,
            first_session_keys=list(sessions[0].keys()) if sessions else None,
        )
        saved_count = await save_planned_sessions(
            user_id=user_id,
            athlete_id=athlete_id,
            sessions=sessions,
            plan_type="race",
            plan_id=plan_id,
        )
        logger.debug(
            "plan_race: save_planned_sessions completed",
            plan_id=plan_id,
            saved_count=saved_count,
            expected_count=len(sessions),
            user_id=user_id,
            athlete_id=athlete_id,
        )

        if saved_count == 0:
            logger.error(
                "Race plan generation failed: no sessions saved",
                plan_id=plan_id,
                total_sessions=len(sessions),
                total_weeks=weeks,
                start_date=start_date_dt.isoformat(),
                race_date=race_date.isoformat(),
                user_id=user_id,
                athlete_id=athlete_id,
            )
            _raise_save_failed_error()

        logger.info(
            "Race plan saved successfully",
            plan_id=plan_id,
            saved_count=saved_count,
            total_sessions=len(sessions),
            user_id=user_id,
            athlete_id=athlete_id,
        )

        target_time_str = f"\nTarget time: {target_time}" if target_time else ""
        save_status = f"• **{saved_count} training sessions** added to your calendar\n"
        calendar_note = "Your planned sessions are now available in your calendar!"

    except Exception as e:
        logger.error(
            "Failed to generate race plan",
            error_type=type(e).__name__,
            error_message=str(e),
            distance=distance,
            race_date=race_date.isoformat(),
            target_time=target_time,
            user_id=user_id,
            athlete_id=athlete_id,
            exc_info=True,
        )
        # Phase 7: User-visible error message
        raise RuntimeError(
            "The AI coach failed to generate a valid training plan. Please retry."
        ) from e

    success_message = (
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
    return (success_message, saved_count)


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


def build_conversation_context(progress: ConversationProgress | None) -> dict[str, str | None]:
    """Build conversation context from ConversationProgress.

    Extracts known facts from progress slots to provide context for extraction.
    Never overwrites filled values with null - context is facts only.

    Args:
        progress: ConversationProgress or None

    Returns:
        Dictionary with conversation context fields
    """
    if not progress:
        return {
            "known_race_name": None,
            "known_race_distance": None,
            "known_race_date": None,
            "known_race_month": None,
            "known_target_time": None,
            "known_goal_type": None,
        }

    slots = progress.slots or {}

    # Extract race_name
    race_name: str | None = None
    if isinstance(slots.get("race_name"), str):
        race_name = slots["race_name"]

    # Extract race_distance
    race_distance: str | None = None
    if isinstance(slots.get("race_distance"), str):
        race_distance = slots["race_distance"]

    # Extract race_date and race_month
    race_date: str | None = None
    race_month: str | None = None
    race_date_value = slots.get("race_date")
    if isinstance(race_date_value, datetime):
        race_date = race_date_value.strftime("%Y-%m-%d")
        race_month = race_date_value.strftime("%B")
    elif isinstance(race_date_value, str):
        # Try to parse if it's a string
        parsed = parse_date_string(race_date_value)
        if parsed:
            race_date = parsed.strftime("%Y-%m-%d")
            race_month = parsed.strftime("%B")

    # Extract target_time
    target_time: str | None = None
    if isinstance(slots.get("target_time"), str):
        target_time = slots["target_time"]

    # Extract goal_type
    goal_type: str | None = None
    if isinstance(slots.get("goal_type"), str):
        goal_type = slots["goal_type"]

    return {
        "known_race_name": race_name,
        "known_race_distance": race_distance,
        "known_race_date": race_date,
        "known_race_month": race_month,
        "known_target_time": target_time,
        "known_goal_type": goal_type,
    }


async def resolve_awaited_slots(
    message: str,
    progress: ConversationProgress,
    today: date,
) -> tuple[dict[str, str | datetime | int | None], list[str]]:
    """Resolve awaited slots from user message using context-aware extraction.

    Uses the new extract_training_goal function with conversation context
    to resolve partial follow-ups correctly.

    Args:
        message: User message
        progress: Conversation progress with current slots and awaiting_slots
        today: Today's date for year inference

    Returns:
        Tuple of (resolved_slots dict, remaining_awaiting_slots list)
    """
    resolved: dict[str, str | datetime | int | None] = {}
    remaining_awaiting = list(progress.awaiting_slots)

    # Build conversation context from progress
    conversation_context = build_conversation_context(progress)

    # Use new extractor with context
    goal_info = await extract_training_goal(
        latest_user_message=message,
        conversation_context=conversation_context,
        awaiting_slots=progress.awaiting_slots,
        today=today,
    )

    # Map extracted fields to slots
    for slot in progress.awaiting_slots:
        if slot == "race_date" and goal_info.race_date:
            parsed_date = parse_date_string(goal_info.race_date)
            if parsed_date:
                resolved["race_date"] = parsed_date
                remaining_awaiting.remove(slot)
                logger.info(
                    "Resolved awaited slot",
                    slot="race_date",
                    value=parsed_date,
                    conversation_id=progress.conversation_id,
                )
        elif slot == "race_distance" and goal_info.race_distance:
            resolved["race_distance"] = goal_info.race_distance
            remaining_awaiting.remove(slot)
            logger.info(
                "Resolved awaited slot",
                slot="race_distance",
                value=goal_info.race_distance,
                conversation_id=progress.conversation_id,
            )
        elif slot == "target_time" and goal_info.target_finish_time:
            resolved["target_time"] = goal_info.target_finish_time
            remaining_awaiting.remove(slot)
            logger.info(
                "Resolved awaited slot",
                slot="target_time",
                value=goal_info.target_finish_time,
                conversation_id=progress.conversation_id,
            )
        elif slot == "race_name" and goal_info.race_name:
            resolved["race_name"] = goal_info.race_name
            remaining_awaiting.remove(slot)
            logger.info(
                "Resolved awaited slot",
                slot="race_name",
                value=goal_info.race_name,
                conversation_id=progress.conversation_id,
            )

    return resolved, remaining_awaiting


async def plan_race_build(
    message: str,
    user_id: str | None = None,
    athlete_id: int | None = None,
    conversation_id: str | None = None,
    return_structured: bool = False,
) -> str | tuple[str, int | None]:
    """Plan a race build and generate training sessions.

    Uses stateful slot extraction with cumulative accumulation and awaited slot resolution.

    Args:
        message: User message containing race details
        user_id: User ID for saving sessions (optional)
        athlete_id: Athlete ID for saving sessions (optional)
        conversation_id: Conversation ID for stateful slot tracking (optional but recommended)
        return_structured: If True, return tuple (message, saved_count). If False, return message string only.

    Returns:
        If return_structured is True: tuple of (response message, saved_count or None)
        If return_structured is False: response message string with plan details or clarification questions
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
        resolved_slots, remaining_awaiting = await resolve_awaited_slots(message, progress, today)

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
            clarification_msg = build_clarification_message(distance, race_date, progress.awaiting_slots)
            if return_structured:
                return (clarification_msg, None)
            return clarification_msg

        # All slots resolved - continue to tool execution
        logger.info(
            "All awaited slots resolved, proceeding to tool execution",
            conversation_id=conversation_id,
        )

    # Build conversation context from progress
    conversation_context = build_conversation_context(progress)
    today = datetime.now(timezone.utc).date()

    # Determine awaiting slots for extraction
    current_awaiting: list[str] = []
    if progress:
        current_awaiting = progress.awaiting_slots or []
    else:
        # If no progress, we need to determine what's missing after extraction
        # For now, extract first, then determine awaiting slots
        current_awaiting = []

    # Extract new slots from current message using context-aware extractor
    goal_info = await extract_training_goal(
        latest_user_message=message,
        conversation_context=conversation_context,
        awaiting_slots=current_awaiting,
        today=today,
    )

    # Map extracted fields to slots
    # Note: slots can contain str, datetime, int, or None (stored as JSON)
    new_slots: dict[str, str | datetime | int | None] = {}
    if goal_info.race_name:
        new_slots["race_name"] = goal_info.race_name
    if goal_info.race_distance:
        new_slots["race_distance"] = goal_info.race_distance
    if goal_info.race_date:
        parsed_date = parse_date_string(goal_info.race_date)
        if parsed_date:
            new_slots["race_date"] = parsed_date
    if goal_info.target_finish_time:
        new_slots["target_time"] = goal_info.target_finish_time
    if goal_info.goal_type:
        new_slots["goal_type"] = goal_info.goal_type
    if goal_info.training_start_date:
        parsed_start = parse_date_string(goal_info.training_start_date)
        if parsed_start:
            new_slots["training_start_date"] = parsed_start
    if goal_info.training_duration_weeks:
        new_slots["training_duration_weeks"] = goal_info.training_duration_weeks

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
        clarification_msg = build_clarification_message(distance, race_date, awaiting_slots)
        if return_structured:
            return (clarification_msg, None)
        return clarification_msg

    # Validate race date is in the future
    if race_date and race_date < datetime.now(timezone.utc):
        error_msg = (
            f"The race date you provided ({race_date.strftime('%Y-%m-%d')}) is in the past. "
            f"Please provide a future race date to generate a training plan."
        )
        if return_structured:
            return (error_msg, None)
        return error_msg

    # Type narrowing: distance and race_date are guaranteed to be non-None here
    if not isinstance(distance, str):
        clarification_msg = build_clarification_message(None, None, ["race_distance"])
        if return_structured:
            return (clarification_msg, None)
        return clarification_msg
    if not isinstance(race_date, datetime):
        clarification_msg = build_clarification_message(None, None, ["race_date"])
        if return_structured:
            return (clarification_msg, None)
        return clarification_msg

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
        message, saved_count = await create_and_save_plan(race_date, distance, target_time, user_id, athlete_id)
        if return_structured:
            return (message, saved_count)
        return message

    # Return plan details without saving
    logger.warning(
        "Missing user_id or athlete_id - returning preview plan",
        user_id=user_id,
        athlete_id=athlete_id,
    )
    preview_msg = _build_preview_plan(distance, race_date)
    if return_structured:
        return (preview_msg, None)
    return preview_msg
