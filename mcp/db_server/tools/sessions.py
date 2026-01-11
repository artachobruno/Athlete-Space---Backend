"""Planned session tools for MCP DB server."""

import asyncio
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.coach.errors import ToolContractViolationError
from app.coach.services.conversation_progress import get_conversation_progress
from app.coach.tools.add_workout import (
    extract_date_from_description,
    parse_workout_details,
)
from app.coach.tools.plan_race import (
    build_clarification_message,
    create_and_save_plan_new,
    extract_race_information,
    parse_date_string,
    plan_race_build,
)
from app.coach.tools.plan_season import (
    generate_season_plan_response,
    parse_season_dates,
)
from app.coach.tools.session_planner import (
    save_sessions_to_database,
)
from app.coach.utils.llm_client import CoachLLMClient
from app.db.models import PlannedSession
from app.db.session import get_session
from mcp.db_server.errors import MCPError


def _raise_missing_race_info(clarification: str) -> NoReturn:
    """Raise MCPError for missing race info."""
    message = clarification.replace("[CLARIFICATION] ", "")
    raise MCPError("MISSING_RACE_INFO", message)


def _raise_invalid_race_input() -> NoReturn:
    """Raise MCPError for invalid race input."""
    raise MCPError("INVALID_INPUT", "Distance and race_date must be provided")


def _raise_invalid_race_date(race_date: datetime) -> NoReturn:
    """Raise MCPError for invalid race date."""
    raise MCPError(
        "INVALID_RACE_DATE",
        f"The race date you provided ({race_date.strftime('%Y-%m-%d')}) is in the past. Please provide a future race date.",
    )


def _raise_missing_season_info() -> NoReturn:
    """Raise MCPError for missing season info."""
    raise MCPError(
        "MISSING_SEASON_INFO",
        "I'd love to create a season training plan for you! To generate your plan, please provide:\n\n"
        "• **Season start date** (e.g., January 1, 2026)\n"
        "• **Season end date** (e.g., December 31, 2026)\n"
        "• **Target races** (optional): List any key races with dates\n"
        "• **Training goals** (optional): What you want to focus on this season\n\n"
        "Once you provide these details, I'll generate a complete season plan with all training sessions "
        "that will be added to your calendar.",
    )


def save_planned_sessions_tool(arguments: dict) -> dict:
    """Save planned training sessions to database.

    Contract: save_planned_sessions.json
    """
    user_id = arguments.get("user_id")
    athlete_id = arguments.get("athlete_id")
    sessions = arguments.get("sessions", [])
    plan_type = arguments.get("plan_type")
    plan_id = arguments.get("plan_id")

    # Validate inputs
    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")
    if athlete_id is None or not isinstance(athlete_id, int):
        raise MCPError("INVALID_INPUT", "Missing or invalid athlete_id")
    if not isinstance(sessions, list):
        raise MCPError("INVALID_SESSION_DATA", "sessions must be an array")
    if not plan_type or not isinstance(plan_type, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid plan_type")

    # Validate session data
    for session_data in sessions:
        if not isinstance(session_data, dict):
            raise MCPError("INVALID_SESSION_DATA", "Each session must be an object")
        if "date" not in session_data or "type" not in session_data or "title" not in session_data:
            raise MCPError("INVALID_SESSION_DATA", "Each session must have date, type, and title")

    try:
        saved_count = save_sessions_to_database(
            user_id=user_id,
            athlete_id=athlete_id,
            sessions=sessions,
            plan_type=plan_type,
            plan_id=plan_id,
        )

        logger.info(f"Saved {saved_count} planned sessions for user_id={user_id}, plan_type={plan_type}")
    except ValueError as e:
        logger.error(f"Validation error saving sessions: {e}", exc_info=True)
        if "date" in str(e).lower() or "format" in str(e).lower():
            raise MCPError("INVALID_DATE_FORMAT", str(e)) from e
        raise MCPError("INVALID_SESSION_DATA", str(e)) from e
    except SQLAlchemyError as e:
        logger.error(f"Database error saving sessions: {e}", exc_info=True)
        raise MCPError("DB_ERROR", "Database insert failed") from e
    except Exception as e:
        logger.error(f"Unexpected error saving sessions: {e}", exc_info=True)
        raise MCPError("DB_ERROR", "Database insert failed") from e
    else:
        return {"saved_count": saved_count}


def add_workout_tool(arguments: dict) -> dict:
    """Add a workout to the training plan.

    Contract: add_workout.json
    """
    workout_description = arguments.get("workout_description")
    user_id = arguments.get("user_id")
    athlete_id = arguments.get("athlete_id")

    # Validate inputs
    if not workout_description or not isinstance(workout_description, str):
        raise MCPError("INVALID_WORKOUT_DESCRIPTION", "Missing or invalid workout_description")
    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")
    if athlete_id is None or not isinstance(athlete_id, int):
        raise MCPError("INVALID_INPUT", "Missing or invalid athlete_id")

    try:
        workout_lower = workout_description.lower()

        # Extract workout details
        title, intensity, duration_minutes, workout_type = parse_workout_details(workout_lower)

        # Determine date (default to tomorrow if not specified)
        workout_date = extract_date_from_description(workout_lower)
        if workout_date is None:
            tomorrow = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            workout_date = tomorrow

        # Create session data
        session_data = {
            "date": workout_date,
            "type": workout_type,
            "title": title,
            "duration_minutes": duration_minutes,
            "intensity": intensity,
            "notes": None,
        }

        # Save session directly to database
        saved_count = save_sessions_to_database(
            user_id=user_id,
            athlete_id=athlete_id,
            sessions=[session_data],
            plan_type="single",
            plan_id=None,
        )

        date_str = workout_date.strftime("%B %d, %Y")
        if saved_count > 0:
            message = f"Workout added successfully. Session saved to your calendar for {date_str}!"
        else:
            message = "Workout added. Note: Session may already exist in your calendar."
    except Exception as e:
        logger.error(f"Error adding workout: {e}", exc_info=True)
        raise MCPError("DB_ERROR", f"Failed to add workout: {e!s}") from e
    else:
        return {
            "status": "success",
            "saved_count": saved_count,
            "message": message,
        }


def plan_race_build_tool(arguments: dict) -> dict:
    """Plan a race build and generate training sessions.

    Contract: plan_race_build.json
    CRITICAL: This tool reads inputs ONLY from filled_slots in context.
    It NEVER re-parses user messages or re-runs NLP extraction.
    """
    message = arguments.get("message")
    user_id = arguments.get("user_id")
    athlete_id = arguments.get("athlete_id")
    conversation_id = arguments.get("conversation_id")  # Optional for backward compatibility
    context = arguments.get("context", {})
    filled_slots = context.get("filled_slots", {})

    # Log tool input contract at entry (B38)
    logger.info(
        "plan_race_build_tool invoked",
        extra={
            "filled_slots": filled_slots,
            "has_context": bool(context),
            "conversation_id": conversation_id,
        },
    )

    # Validate inputs
    if not message or not isinstance(message, str):
        raise MCPError("INVALID_MESSAGE", "Missing or invalid message")
    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")
    if athlete_id is None or not isinstance(athlete_id, int):
        raise MCPError("INVALID_INPUT", "Missing or invalid athlete_id")

    # B37: Read inputs ONLY from filled_slots - fail hard if slots are missing
    race_date_value = filled_slots.get("race_date")
    race_distance = filled_slots.get("race_distance")

    if not race_date_value:
        raise ToolContractViolationError(
            "plan_race_build",
            "plan_race_build_tool invoked without race_date in filled_slots",
        )

    if not race_distance:
        raise ToolContractViolationError(
            "plan_race_build",
            "plan_race_build_tool invoked without race_distance in filled_slots",
        )

    # Convert race_date_value to datetime if needed
    if isinstance(race_date_value, str):
        race_date_parsed = parse_date_string(race_date_value)
        if not race_date_parsed:
            raise ToolContractViolationError(
                "plan_race_build",
                f"race_date in filled_slots is invalid: {race_date_value}",
            )
        race_date = race_date_parsed
    elif isinstance(race_date_value, datetime):
        race_date = race_date_value
    else:
        raise ToolContractViolationError(
            "plan_race_build",
            f"race_date in filled_slots has invalid type: {type(race_date_value)}",
        )

    if not isinstance(race_distance, str):
        raise ToolContractViolationError(
            "plan_race_build",
            f"race_distance in filled_slots has invalid type: {type(race_distance)}",
        )

    target_time = filled_slots.get("target_time")
    target_time_str = target_time if isinstance(target_time, str) else None

    logger.debug(
        "plan_race_build_tool: Validated inputs, calling create_and_save_plan_new",
        race_date=race_date.isoformat() if isinstance(race_date, datetime) else str(race_date),
        race_distance=race_distance,
        target_time=target_time_str,
        user_id=user_id,
        athlete_id=athlete_id,
        filled_slots_keys=list(filled_slots.keys()),
    )

    try:
        # B37: Use slots directly from filled_slots - no re-extraction
        # Call create_and_save_plan_new with validated slots
        logger.debug(
            "plan_race_build_tool: Starting create_and_save_plan_new via asyncio.run",
            race_date=race_date.isoformat() if isinstance(race_date, datetime) else str(race_date),
            distance=race_distance,
            target_time=target_time_str,
        )
        result_message, saved_count = asyncio.run(
            create_and_save_plan_new(
                race_date=race_date,
                distance=race_distance,
                target_time=target_time_str,
                user_id=user_id,
                athlete_id=athlete_id,
                conversation_id=conversation_id,
            )
        )
        logger.debug(
            "plan_race_build_tool: create_and_save_plan_new completed",
            saved_count=saved_count,
            message_length=len(result_message) if result_message else 0,
            race_distance=race_distance,
            race_date=race_date.isoformat() if isinstance(race_date, datetime) else str(race_date),
        )
    except MCPError as e:
        logger.debug(
            "plan_race_build_tool: MCPError caught, re-raising",
            error_code=e.code,
            error_message=e.message,
        )
        raise
    except ToolContractViolationError as e:
        # Convert ToolContractViolationError to MCPError with specific code (developer error)
        logger.debug(
            "plan_race_build_tool: ToolContractViolationError caught",
            tool_name=e.tool_name,
            error_message=e.message,
        )
        logger.error(
            "Tool contract violation detected",
            tool="plan_race_build",
            error=str(e),
            exc_info=True,
        )
        raise MCPError("TOOL_CONTRACT_VIOLATION", f"Tool contract violation: {e.message}") from e
    except Exception as e:
        # Extract original error details from exception chain
        original_error = e.__cause__ if e.__cause__ else e
        original_error_type = type(original_error).__name__
        original_error_message = str(original_error)
        error_details = f"{original_error_type}: {original_error_message}"

        logger.debug(
            "plan_race_build_tool: Exception caught during plan creation",
            error_type=type(e).__name__,
            error_message=str(e),
            error_class=type(e).__module__ + "." + type(e).__name__,
            has_cause=bool(e.__cause__),
            original_error_type=original_error_type if e.__cause__ else None,
            original_error_message=original_error_message if e.__cause__ else None,
            race_date=race_date.isoformat() if isinstance(race_date, datetime) else str(race_date),
            race_distance=race_distance,
            target_time=target_time_str,
        )
        logger.error(
            f"Error planning race build: {e}",
            error_type=type(e).__name__,
            error_message=str(e),
            has_cause=bool(e.__cause__),
            original_error_type=original_error_type if e.__cause__ else None,
            original_error_message=original_error_message if e.__cause__ else None,
            exc_info=True,
        )
        # Include original error details in MCP error message if available
        if e.__cause__:
            error_msg = f"Failed to plan race build: {error_details} (wrapped: {type(e).__name__}: {e!s})"
        else:
            error_msg = f"Failed to plan race build: {error_details}"
        raise MCPError("DB_ERROR", error_msg) from e
    else:
        # B37: Use slots directly - no re-extraction from message
        # All required data is already validated from filled_slots above
        result = {
            "status": "success",
            "saved_count": saved_count or 0,
            "message": result_message,
            "race_distance": race_distance,
            "race_date": race_date.isoformat() if isinstance(race_date, datetime) else str(race_date),
        }
        logger.debug(
            "plan_race_build_tool: Returning success result",
            saved_count=result.get("saved_count"),
            has_message=bool(result.get("message")),
        )
        return result


def plan_season_tool(arguments: dict) -> dict:
    """Generate a season training plan with sessions.

    Contract: plan_season.json
    """
    message = arguments.get("message", "")
    user_id = arguments.get("user_id")
    athlete_id = arguments.get("athlete_id")

    # Validate inputs
    if not isinstance(message, str):
        raise MCPError("INVALID_MESSAGE", "message must be a string")
    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")
    if athlete_id is None or not isinstance(athlete_id, int):
        raise MCPError("INVALID_INPUT", "Missing or invalid athlete_id")

    try:
        message_lower = message.lower() if message else ""

        # Extract season dates
        season_start, season_end = parse_season_dates(message_lower)

        # Check if we need more info
        if not message or ("season" not in message_lower and "plan" not in message_lower):
            _raise_missing_season_info()

        # Generate plan via LLM - single source of truth
        llm_client = CoachLLMClient()
        goal_context = {
            "plan_type": "season",
            "season_start": season_start.isoformat(),
            "season_end": season_end.isoformat(),
        }
        user_context = {
            "user_id": user_id,
            "athlete_id": athlete_id,
        }
        athlete_context = {}  # TODO: Fill with actual athlete context
        calendar_constraints = {}  # TODO: Fill with actual calendar constraints

        training_plan = asyncio.run(
            llm_client.generate_training_plan_via_llm(
                user_context=user_context,
                athlete_context=athlete_context,
                goal_context=goal_context,
                calendar_constraints=calendar_constraints,
            )
        )

        # Convert TrainingPlan to session dictionaries
        # Phase 6: Persist exactly what LLM returns - only minimal field name mapping for DB compatibility
        sessions = []
        for session in training_plan.sessions:
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

        plan_id = f"season_{season_start.strftime('%Y%m%d')}_{season_end.strftime('%Y%m%d')}"
        saved_count = save_sessions_to_database(
            user_id=user_id,
            athlete_id=athlete_id,
            sessions=sessions,
            plan_type="season",
            plan_id=plan_id,
        )

        weeks = (season_end - season_start).days // 7
        result_message = generate_season_plan_response(season_start, season_end, saved_count, weeks)

        return {
            "status": "success",
            "saved_count": saved_count,
            "message": result_message,
            "season_start": season_start.isoformat(),
            "season_end": season_end.isoformat(),
        }

    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error planning season: {e}", exc_info=True)
        raise MCPError("DB_ERROR", f"Failed to plan season: {e!s}") from e
