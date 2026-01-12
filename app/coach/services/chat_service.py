"""Coach chat service - core logic shared between API and CLI."""

import asyncio
import contextlib
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.agents.orchestrator_deps import AthleteProfileData, CoachDeps, RaceProfileData, TrainingPreferencesData
from app.coach.execution_guard import TurnExecutionGuard
from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.mcp_client import MCPError, call_tool, emit_progress_event_safe
from app.coach.services.state_builder import build_athlete_state
from app.coach.tools.cold_start import welcome_new_user
from app.db.models import AthleteProfile, StravaAccount, UserSettings
from app.db.session import get_session
from app.state.api_helpers import get_training_data, get_user_id_from_athlete_id


async def process_coach_chat(
    message: str,
    user_id: str,
    athlete_id: int,
    conversation_id: str,
    *,
    days: int = 60,
    days_to_race: int | None = None,
) -> str:
    """Process coach chat message - core logic shared between API and CLI.

    This function contains the core orchestrator + executor logic without
    FastAPI-specific concerns (normalization, Redis, Postgres persistence).

    Args:
        message: User's message
        user_id: User ID
        athlete_id: Athlete ID
        conversation_id: Conversation ID
        days: Number of days of training data to consider
        days_to_race: Optional days until race

    Returns:
        Coach's reply message
    """
    logger.info(
        "Processing coach chat",
        message_length=len(message),
        user_id=user_id,
        athlete_id=athlete_id,
        conversation_id=conversation_id,
    )

    # Check if this is a cold start (empty history)
    history_empty = await _is_history_empty(athlete_id)
    logger.debug(
        "Cold start check result",
        conversation_id=conversation_id,
        athlete_id=athlete_id,
        history_empty=history_empty,
    )

    # Handle cold start
    if history_empty:
        logger.info(
            "Cold start detected - providing welcome message",
            conversation_id=conversation_id,
        )
        try:
            training_data = get_training_data(user_id=user_id, days=days)
            athlete_state = build_athlete_state(
                ctl=training_data.ctl,
                atl=training_data.atl,
                tsb=training_data.tsb,
                daily_load=training_data.daily_load,
                days_to_race=days_to_race,
            )
            logger.debug(
                "Cold start with training data",
                conversation_id=conversation_id,
                athlete_id=athlete_id,
                ctl=athlete_state.ctl,
                atl=athlete_state.atl,
                tsb=athlete_state.tsb,
            )
            return welcome_new_user(athlete_state)
        except RuntimeError as e:
            logger.warning(
                "Cold start with no training data available",
                conversation_id=conversation_id,
                error=str(e),
            )
            return welcome_new_user(None)

    # Fast-path: Handle simple activity acknowledgments
    if _is_simple_acknowledgment(message):
        logger.info(
            "Fast-path: Handling simple acknowledgment",
            conversation_id=conversation_id,
            message=message,
            athlete_id=athlete_id,
        )
        return "Nice work 👍 Want feedback on recovery, pacing, or tomorrow's plan?"

    # Build athlete state
    try:
        training_data = get_training_data(user_id=user_id, days=days)
        athlete_state = build_athlete_state(
            ctl=training_data.ctl,
            atl=training_data.atl,
            tsb=training_data.tsb,
            daily_load=training_data.daily_load,
            days_to_race=days_to_race,
        )
    except RuntimeError:
        logger.warning(
            "No training data available for orchestrator",
            conversation_id=conversation_id,
        )
        athlete_state = None

    # Load athlete profile, training preferences, and race profile
    athlete_profile = None
    training_preferences = None
    race_profile = None
    with get_session() as db:
        profile = db.query(AthleteProfile).filter_by(user_id=user_id).first()
        if profile:
            # Calculate age from date_of_birth
            age = None
            if profile.date_of_birth:
                today = datetime.now(timezone.utc).date()
                dob = profile.date_of_birth.date()
                age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

            # Round weight_lbs and height_in to 1 decimal place
            weight_lbs_rounded = None
            if profile.weight_lbs is not None:
                weight_lbs_rounded = round(float(profile.weight_lbs), 1)
            height_in_rounded = None
            if profile.height_in is not None:
                height_in_rounded = round(float(profile.height_in), 1)

            athlete_profile = AthleteProfileData(
                gender=profile.gender,
                age=age,
                weight_lbs=weight_lbs_rounded,
                height_in=height_in_rounded,
                unit_system=profile.unit_system or "imperial",
            )

            # Load race profile from extracted_race_attributes
            if profile.extracted_race_attributes and isinstance(profile.extracted_race_attributes, dict):
                race_attrs = profile.extracted_race_attributes
                race_profile = RaceProfileData(
                    event_name=race_attrs.get("event_name"),
                    event_type=race_attrs.get("event_type"),
                    event_date=race_attrs.get("event_date"),
                    target_time=race_attrs.get("target_time"),
                    distance=race_attrs.get("distance"),
                    location=race_attrs.get("location"),
                    raw_text=race_attrs.get("raw_text"),
                )

        # Load training preferences from UserSettings
        settings = db.query(UserSettings).filter_by(user_id=user_id).first()
        if settings:
            training_preferences = TrainingPreferencesData(
                training_consistency=settings.consistency,
                years_structured=settings.years_of_training,
                primary_sports=settings.primary_sports or [],
                available_days=settings.available_days or [],
                weekly_training_hours=settings.weekly_hours,
                primary_training_goal=settings.goal,
                training_focus=settings.training_focus,
                injury_flag=settings.injury_history or False,
            )

    # Create turn-scoped execution guard (prevents duplicate tool execution within a turn)
    execution_guard = TurnExecutionGuard(conversation_id=conversation_id)
    logger.debug(
        "Initialized execution guard for turn",
        conversation_id=conversation_id,
    )

    # Create dependencies
    deps = CoachDeps(
        athlete_id=athlete_id,
        user_id=user_id,
        athlete_state=athlete_state,
        athlete_profile=athlete_profile,
        training_preferences=training_preferences,
        race_profile=race_profile,
        days=days,
        days_to_race=days_to_race,
        execution_guard=execution_guard,
    )

    # Get decision from orchestrator (pass conversation_id for slot persistence)
    decision = await run_conversation(
        user_input=message,
        deps=deps,
        conversation_id=conversation_id,
    )

    # CRITICAL: Emit planned events ONLY if action is EXECUTE
    # NO_ACTION must be pure - no side effects, no events, no DB writes
    if decision.action == "EXECUTE" and decision.action_plan:
        logger.info(
            "Emitting planned events for action plan",
            conversation_id=conversation_id,
            step_count=len(decision.action_plan.steps),
        )
        for step in decision.action_plan.steps:
            await emit_progress_event_safe(
                conversation_id=conversation_id,
                step_id=step.id,
                label=step.label,
                status="planned",
            )

    # Execute action if needed (executor will also guard against NO_ACTION)
    return await CoachActionExecutor.execute(decision, deps, conversation_id=conversation_id)


async def _is_history_empty(athlete_id: int | None = None) -> bool:
    """Check if coach chat history is empty for an athlete.

    Args:
        athlete_id: Athlete ID to check history for

    Returns:
        True if history is empty, False otherwise
    """
    if athlete_id is None:
        return True

    try:
        result = await call_tool("load_context", {"athlete_id": athlete_id, "limit": 1})
        messages = result.get("messages", [])
        return len(messages) == 0
    except Exception as e:
        logger.warning(f"Failed to check history: {e}")
        return True


def _is_simple_acknowledgment(message: str) -> bool:
    """Check if message is a simple acknowledgment that can be fast-pathed.

    Args:
        message: User message to check

    Returns:
        True if message is a simple acknowledgment
    """
    message_lower = message.lower().strip()
    acknowledgments = [
        "thanks",
        "thank you",
        "thx",
        "ty",
        "ok",
        "okay",
        "got it",
        "sounds good",
        "cool",
        "nice",
        "👍",
        "👌",
    ]
    return message_lower in acknowledgments


def dispatch_coach_chat(
    message: str,
    athlete_id: int,
    days: int = 60,
    days_to_race: int | None = None,
    history_empty: bool = False,
) -> tuple[str, str]:
    """Synchronous wrapper for coach chat that returns (intent, reply).

    This function is a backward-compatibility wrapper for legacy endpoints
    that expect a synchronous function returning (intent, reply).

    Args:
        message: User's message
        athlete_id: Athlete ID
        days: Number of days of training data to consider
        days_to_race: Optional days until race
        history_empty: Whether conversation history is empty

    Returns:
        Tuple of (intent, reply) where intent is the orchestrator intent classification
    """
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.warning(f"Cannot find user_id for athlete_id={athlete_id}")
        return ("error", "Unable to find user account. Please reconnect your Strava account.")

    conversation_id = f"sync_{athlete_id}_{datetime.now(timezone.utc).timestamp()}"

    async def _async_dispatch() -> tuple[str, str]:
        """Async helper that runs the chat logic and preserves intent."""
        if history_empty:
            try:
                training_data = get_training_data(user_id=user_id, days=days)
                athlete_state = build_athlete_state(
                    ctl=training_data.ctl,
                    atl=training_data.atl,
                    tsb=training_data.tsb,
                    daily_load=training_data.daily_load,
                    days_to_race=days_to_race,
                )
                reply = welcome_new_user(athlete_state)
            except RuntimeError:
                reply = welcome_new_user(None)
            return ("cold_start", reply)

        if _is_simple_acknowledgment(message):
            return ("general", "Nice work 👍 Want feedback on recovery, pacing, or tomorrow's plan?")

        try:
            training_data = get_training_data(user_id=user_id, days=days)
            athlete_state = build_athlete_state(
                ctl=training_data.ctl,
                atl=training_data.atl,
                tsb=training_data.tsb,
                daily_load=training_data.daily_load,
                days_to_race=days_to_race,
            )
        except RuntimeError:
            athlete_state = None

        athlete_profile = None
        training_preferences = None
        race_profile = None
        with get_session() as db:
            profile = db.query(AthleteProfile).filter_by(user_id=user_id).first()
            if profile:
                age = None
                if profile.date_of_birth:
                    today = datetime.now(timezone.utc).date()
                    dob = profile.date_of_birth.date()
                    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

                weight_lbs_rounded = None
                if profile.weight_lbs is not None:
                    weight_lbs_rounded = round(float(profile.weight_lbs), 1)
                height_in_rounded = None
                if profile.height_in is not None:
                    height_in_rounded = round(float(profile.height_in), 1)

                athlete_profile = AthleteProfileData(
                    gender=profile.gender,
                    age=age,
                    weight_lbs=weight_lbs_rounded,
                    height_in=height_in_rounded,
                    unit_system=profile.unit_system or "imperial",
                )

                if profile.extracted_race_attributes and isinstance(profile.extracted_race_attributes, dict):
                    race_attrs = profile.extracted_race_attributes
                    race_profile = RaceProfileData(
                        event_name=race_attrs.get("event_name"),
                        event_type=race_attrs.get("event_type"),
                        event_date=race_attrs.get("event_date"),
                        target_time=race_attrs.get("target_time"),
                        distance=race_attrs.get("distance"),
                        location=race_attrs.get("location"),
                        raw_text=race_attrs.get("raw_text"),
                    )

            settings = db.query(UserSettings).filter_by(user_id=user_id).first()
            if settings:
                training_preferences = TrainingPreferencesData(
                    training_consistency=settings.consistency,
                    years_structured=settings.years_of_training,
                    primary_sports=settings.primary_sports or [],
                    available_days=settings.available_days or [],
                    weekly_training_hours=settings.weekly_hours,
                    primary_training_goal=settings.goal,
                    training_focus=settings.training_focus,
                    injury_flag=settings.injury_history or False,
                )

        # Create turn-scoped execution guard (prevents duplicate tool execution within a turn)
        execution_guard = TurnExecutionGuard(conversation_id=conversation_id)
        logger.debug(
            "Initialized execution guard for turn",
            conversation_id=conversation_id,
        )

        deps = CoachDeps(
            athlete_id=athlete_id,
            user_id=user_id,
            athlete_state=athlete_state,
            athlete_profile=athlete_profile,
            training_preferences=training_preferences,
            race_profile=race_profile,
            days=days,
            days_to_race=days_to_race,
            execution_guard=execution_guard,
        )

        decision = await run_conversation(
            user_input=message,
            deps=deps,
            conversation_id=conversation_id,
        )

        if decision.action == "EXECUTE" and decision.action_plan:
            for step in decision.action_plan.steps:
                await emit_progress_event_safe(
                    conversation_id=conversation_id,
                    step_id=step.id,
                    label=step.label,
                    status="planned",
                )

        reply = await CoachActionExecutor.execute(decision, deps, conversation_id=conversation_id)
        return (decision.intent, reply)

    try:
        # Since this is called from sync endpoints, FastAPI runs them in a thread pool
        # which doesn't have an event loop, so asyncio.run() is safe to use
        return asyncio.run(_async_dispatch())
    except Exception as e:
        logger.exception(f"Error in dispatch_coach_chat: {e}")
        return ("error", "I encountered an error processing your question. Please try again.")
