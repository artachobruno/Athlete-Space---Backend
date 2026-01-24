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
from app.coach.executor.errors import ExecutionError, InvalidModificationSpecError, NoActionError, PersistenceError
from app.coach.mcp_client import MCPError, call_tool, emit_progress_event_safe
from app.coach.services.state_builder import build_athlete_state
from app.coach.tools.cold_start import welcome_new_user
from app.db.models import AthleteProfile, StravaAccount, UserSettings
from app.db.session import get_session
from app.responses.input_builder import build_style_input
from app.responses.style_llm import generate_coach_message
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
    # Resolve user_id from athlete_id if user_id is the CLI default placeholder
    resolved_user_id = user_id
    if user_id == "cli-user":
        resolved_user_id = get_user_id_from_athlete_id(athlete_id)
        if resolved_user_id is None:
            logger.warning(
                "Cannot resolve user_id from athlete_id for CLI",
                athlete_id=athlete_id,
                conversation_id=conversation_id,
            )
            # Fall back to a UUID-like placeholder to avoid DB errors, but this won't work
            # The CLI should provide a valid user_id or athlete_id should be in the database
            raise ValueError(
                f"Cannot find user_id for athlete_id={athlete_id}. "
                "Please ensure the athlete has a Strava account connected."
            )
        logger.info(
            "Resolved user_id from athlete_id for CLI",
            original_user_id=user_id,
            resolved_user_id=resolved_user_id,
            athlete_id=athlete_id,
        )

    logger.info(
        "Processing coach chat",
        message_length=len(message),
        user_id=resolved_user_id,
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

    # Handle cold start - only return welcome message for simple greetings
    # For actual queries/questions, process them normally even on cold start
    if history_empty and _is_simple_greeting(message):
        logger.info(
            "Cold start with greeting - providing welcome message",
            conversation_id=conversation_id,
        )
        try:
            training_data = get_training_data(user_id=resolved_user_id, days=days)
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
        training_data = get_training_data(user_id=resolved_user_id, days=days)
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
        profile = db.query(AthleteProfile).filter_by(user_id=resolved_user_id).first()
        if profile:
            # Calculate age from date_of_birth
            age = None
            dob = getattr(profile, "date_of_birth", None)
            if dob:
                today = datetime.now(timezone.utc).date()
                dob_date = dob.date()
                age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))

            # Round weight_lbs and height_in to 1 decimal place
            weight_lbs_rounded = None
            weight_lbs = getattr(profile, "weight_lbs", None)
            if weight_lbs is not None:
                weight_lbs_rounded = round(float(weight_lbs), 1)
            height_in_rounded = None
            height_in = getattr(profile, "height_in", None)
            if height_in is not None:
                height_in_rounded = round(float(height_in), 1)

            athlete_profile = AthleteProfileData(
                gender=getattr(profile, "gender", None),
                age=age,
                weight_lbs=weight_lbs_rounded,
                height_in=height_in_rounded,
                unit_system=getattr(profile, "unit_system", None) or "imperial",
            )

            # Load race profile from extracted_race_attributes
            extracted_race_attributes = getattr(profile, "extracted_race_attributes", None)
            if extracted_race_attributes and isinstance(extracted_race_attributes, dict):
                race_attrs = extracted_race_attributes
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
        settings = db.query(UserSettings).filter_by(user_id=resolved_user_id).first()
        if settings:
            training_preferences = TrainingPreferencesData(
                training_consistency=getattr(settings, "consistency", None),
                years_structured=getattr(settings, "years_of_training", None),
                primary_sports=getattr(settings, "primary_sports", None) or [],
                available_days=getattr(settings, "available_days", None) or [],
                weekly_training_hours=getattr(settings, "weekly_hours", None),
                primary_training_goal=getattr(settings, "goal", None),
                training_focus=getattr(settings, "training_focus", None),
                injury_flag=getattr(settings, "injury_history", None) or False,
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
        user_id=resolved_user_id,
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

    try:
        executor_reply = await CoachActionExecutor.execute(
            decision, deps, conversation_id=conversation_id, user_message=message
        )
    except PersistenceError:
        return (
            "I couldn't save your training plan to your calendar. "
            "Nothing was changed. Please try again in a moment."
        )
    except ExecutionError:
        raise
    except (NoActionError, InvalidModificationSpecError):
        return "I need a bit more detail before I can make that change. What would you like to modify?"
    except MCPError as e:
        logger.warning("MCP tool error during execution", error_code=e.code, message=e.message)
        return "Something went wrong while handling your request. Please try again."

    # Style LLM: Rewrite structured decision into natural coach message
    # This is NON-AUTHORITATIVE - it rewrites, but never decides, computes, retrieves, or executes
    reply = executor_reply
    try:
        # Only use Style LLM for summary/explanation responses (informational queries)
        # For planning responses, use executor reply as-is
        if decision.response_type in {"summary", "explanation"} and decision.action == "EXECUTE":
            style_input = build_style_input(
                decision=decision,
                executor_reply=executor_reply,
                athlete_state=deps.athlete_state,
            )
            styled_reply = await generate_coach_message(style_input)
            logger.info(
                "Style LLM rewrote executor reply",
                response_type=decision.response_type,
                original_length=len(executor_reply),
                styled_length=len(styled_reply),
            )
            reply = styled_reply
    except Exception as e:
        # Fallback to executor reply if Style LLM fails
        logger.warning(
            "Style LLM failed, using executor reply",
            error=str(e),
            error_type=type(e).__name__,
        )
        # reply already set to executor_reply above

    return reply


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


def _is_simple_greeting(message: str) -> bool:
    """Check if message is a simple greeting (not a query).

    Args:
        message: User message to check

    Returns:
        True if message is a simple greeting without a query
    """
    message_lower = message.lower().strip()
    greetings = [
        "hi",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "hey there",
        "good morning",
        "good afternoon",
        "good evening",
        "",
    ]
    # Check if message is just a greeting (exact match or starts with greeting)
    if message_lower in greetings:
        return True
    # Check if message is just a greeting followed by nothing meaningful
    for greeting in greetings:
        if greeting and message_lower.startswith(greeting):
            # If the message is just the greeting or greeting + punctuation/whitespace
            remainder = message_lower[len(greeting):].strip()
            if not remainder or remainder in {".", "!", "?", ",", ":", ";"}:
                return True
    return False


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
                dob = getattr(profile, "date_of_birth", None)
                if dob:
                    today = datetime.now(timezone.utc).date()
                    dob_date = dob.date()
                    age = today.year - dob_date.year - ((today.month, today.day) < (dob_date.month, dob_date.day))

                weight_lbs_rounded = None
                weight_lbs = getattr(profile, "weight_lbs", None)
                if weight_lbs is not None:
                    weight_lbs_rounded = round(float(weight_lbs), 1)
                height_in_rounded = None
                height_in = getattr(profile, "height_in", None)
                if height_in is not None:
                    height_in_rounded = round(float(height_in), 1)

                athlete_profile = AthleteProfileData(
                    gender=getattr(profile, "gender", None),
                    age=age,
                    weight_lbs=weight_lbs_rounded,
                    height_in=height_in_rounded,
                    unit_system=getattr(profile, "unit_system", None) or "imperial",
                )

                extracted_race_attributes = getattr(profile, "extracted_race_attributes", None)
                if extracted_race_attributes and isinstance(extracted_race_attributes, dict):
                    race_attrs = extracted_race_attributes
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
                    training_consistency=getattr(settings, "consistency", None),
                    years_structured=getattr(settings, "years_of_training", None),
                    primary_sports=getattr(settings, "primary_sports", None) or [],
                    available_days=getattr(settings, "available_days", None) or [],
                    weekly_training_hours=getattr(settings, "weekly_hours", None),
                    primary_training_goal=getattr(settings, "goal", None),
                    training_focus=getattr(settings, "training_focus", None),
                    injury_flag=getattr(settings, "injury_history", None) or False,
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

        try:
            reply = await CoachActionExecutor.execute(
                decision, deps, conversation_id=conversation_id, user_message=message
            )
        except PersistenceError:
            return (
                "error",
                "I couldn't save your training plan to your calendar. Nothing was changed. Please try again in a moment.",
            )
        except ExecutionError:
            raise
        except (NoActionError, InvalidModificationSpecError):
            return (
                "clarify",
                "I need a bit more detail before I can make that change. What would you like to modify?",
            )
        return (decision.intent, reply)

    try:
        # Since this is called from sync endpoints, FastAPI runs them in a thread pool
        # which doesn't have an event loop, so asyncio.run() is safe to use
        return asyncio.run(_async_dispatch())
    except Exception as e:
        logger.exception(f"Error in dispatch_coach_chat: {e}")
        return ("error", "I encountered an error processing your question. Please try again.")
