"""Planned session tools for MCP DB server."""

import asyncio
import re
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import NoReturn

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.coach.services.conversation_progress import get_conversation_progress
from app.coach.tools.add_workout import (
    extract_date_from_description,
    parse_workout_details,
)
from app.coach.tools.plan_race import (
    build_clarification_message,
    create_and_save_plan,
    extract_race_information,
    parse_date_string,
    plan_race_build,
)
from app.coach.tools.plan_season import (
    generate_season_plan_response,
    parse_season_dates,
)
from app.coach.tools.session_planner import (
    generate_race_build_sessions,
    generate_season_sessions,
    save_sessions_to_database,
)
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
            "success": True,
            "message": message,
            "saved_count": saved_count,
        }


def plan_race_build_tool(arguments: dict) -> dict:
    """Plan a race build and generate training sessions.

    Contract: plan_race_build.json
    Uses stateful slot extraction with cumulative accumulation.
    """
    message = arguments.get("message")
    user_id = arguments.get("user_id")
    athlete_id = arguments.get("athlete_id")
    conversation_id = arguments.get("conversation_id")  # Optional for backward compatibility

    # Validate inputs
    if not message or not isinstance(message, str):
        raise MCPError("INVALID_MESSAGE", "Missing or invalid message")
    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")
    if athlete_id is None or not isinstance(athlete_id, int):
        raise MCPError("INVALID_INPUT", "Missing or invalid athlete_id")

    try:
        # Call the stateful plan_race_build function
        # This handles slot extraction, merging, and awaited slot resolution
        result_message = asyncio.run(
            plan_race_build(
                message=message,
                user_id=user_id,
                athlete_id=athlete_id,
                conversation_id=conversation_id,
            )
        )

        # Check if this is a clarification message
        if result_message.startswith("[CLARIFICATION]"):
            # Extract the message without the prefix
            clarification = result_message.replace("[CLARIFICATION] ", "")
            _raise_missing_race_info(clarification)

        # If we got here, the plan was created successfully
        # The sessions have already been saved by create_and_save_plan
        # Extract race info to return in response
        race_info = extract_race_information(message)
        distance = race_info.distance
        race_date = parse_date_string(race_info.date) if race_info.date else None

        # Try to get from conversation progress if available
        if conversation_id:
            progress = get_conversation_progress(conversation_id)
            if progress:
                distance = progress.slots.get("race_distance") or distance
                race_date_val = progress.slots.get("race_date")
                if race_date_val and isinstance(race_date_val, datetime):
                    race_date = race_date_val

        # Extract saved_count from result message (sessions already saved by plan_race_build)
        # Parse the message to find saved count, or default to 0 if parsing fails
        saved_count = 0
        if "training sessions" in result_message:
            match = re.search(r"(\d+)\s+training sessions", result_message)
            if match:
                saved_count = int(match.group(1))

        return {
            "success": True,
            "message": result_message,
            "saved_count": saved_count,
            "race_distance": distance or "",
            "race_date": race_date.isoformat() if race_date else "",
        }

    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error planning race build: {e}", exc_info=True)
        raise MCPError("DB_ERROR", f"Failed to plan race build: {e!s}") from e


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

        # Generate sessions
        sessions = generate_season_sessions(
            season_start=season_start,
            season_end=season_end,
            _target_races=None,
        )

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
            "success": True,
            "message": result_message,
            "saved_count": saved_count,
            "season_start": season_start.isoformat(),
            "season_end": season_end.isoformat(),
        }

    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error planning season: {e}", exc_info=True)
        raise MCPError("DB_ERROR", f"Failed to plan season: {e!s}") from e
