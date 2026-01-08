"""Coach analysis tools for MCP DB server.

These tools handle coach-specific operations that require athlete state.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.coach.schemas.athlete_state import AthleteState
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_week import plan_week
from app.coach.tools.run_analysis import run_analysis
from app.coach.tools.share_report import share_report
from app.db.models import PlannedSession
from app.db.session import get_session
from mcp.db_server.errors import MCPError


def _parse_athlete_state(state_dict: dict) -> AthleteState:
    """Parse athlete state from dictionary.

    Args:
        state_dict: Dictionary representation of AthleteState

    Returns:
        AthleteState object

    Raises:
        MCPError: If state_dict is invalid
    """
    try:
        return AthleteState(**state_dict)
    except Exception as e:
        raise MCPError("INVALID_STATE", f"Invalid athlete state: {e!s}") from e


def plan_week_tool(arguments: dict) -> dict:
    """Plan weekly training sessions.

    Contract: plan_week.json
    """
    state_dict = arguments.get("state")
    user_id = arguments.get("user_id")
    athlete_id = arguments.get("athlete_id")

    if not state_dict or not isinstance(state_dict, dict):
        raise MCPError("INVALID_INPUT", "Missing or invalid state")

    try:
        state = _parse_athlete_state(state_dict)
        result = plan_week(state, user_id, athlete_id)
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error planning week: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to plan week: {e!s}") from e
    else:
        return {"message": result}


def run_analysis_tool(arguments: dict) -> dict:
    """Run training analysis.

    Contract: run_analysis.json
    """
    state_dict = arguments.get("state")

    if not state_dict or not isinstance(state_dict, dict):
        raise MCPError("INVALID_INPUT", "Missing or invalid state")

    try:
        state = _parse_athlete_state(state_dict)
        result = run_analysis(state)
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error running analysis: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to run analysis: {e!s}") from e
    else:
        return {"message": result}


def explain_training_state_tool(arguments: dict) -> dict:
    """Explain current training state.

    Contract: explain_training_state.json
    """
    state_dict = arguments.get("state")

    if not state_dict or not isinstance(state_dict, dict):
        raise MCPError("INVALID_INPUT", "Missing or invalid state")

    try:
        state = _parse_athlete_state(state_dict)
        result = explain_training_state(state)
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error explaining training state: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to explain training state: {e!s}") from e
    else:
        return {"message": result}


def adjust_training_load_tool(arguments: dict) -> dict:
    """Adjust training load based on feedback.

    Contract: adjust_training_load.json
    """
    state_dict = arguments.get("state")
    user_feedback = arguments.get("user_feedback")

    if not state_dict or not isinstance(state_dict, dict):
        raise MCPError("INVALID_INPUT", "Missing or invalid state")
    if not user_feedback or not isinstance(user_feedback, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_feedback")

    try:
        state = _parse_athlete_state(state_dict)
        result = adjust_training_load(state, user_feedback)
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error adjusting training load: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to adjust training load: {e!s}") from e
    else:
        return {"message": result}


async def recommend_next_session_tool_impl(state: AthleteState, user_id: str | None) -> str:
    """Async wrapper for recommend_next_session."""
    return await recommend_next_session(state, user_id)


def recommend_next_session_tool(arguments: dict) -> dict:
    """Recommend next training session.

    Contract: recommend_next_session.json
    """
    state_dict = arguments.get("state")
    user_id = arguments.get("user_id")

    if not state_dict or not isinstance(state_dict, dict):
        raise MCPError("INVALID_INPUT", "Missing or invalid state")

    try:
        state = _parse_athlete_state(state_dict)
        result = asyncio.run(recommend_next_session_tool_impl(state, user_id))
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error recommending next session: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to recommend next session: {e!s}") from e
    else:
        return {"message": result}


def share_report_tool(arguments: dict) -> dict:
    """Generate shareable training report.

    Contract: share_report.json
    """
    state_dict = arguments.get("state")

    if not state_dict or not isinstance(state_dict, dict):
        raise MCPError("INVALID_INPUT", "Missing or invalid state")

    try:
        state = _parse_athlete_state(state_dict)
        result = share_report(state)
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error generating report: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to generate report: {e!s}") from e
    else:
        return {"message": result}


def get_planned_sessions_tool(arguments: dict) -> dict:
    """Get planned training sessions (read-only query).

    Contract: get_planned_sessions.json
    """
    user_id = arguments.get("user_id")
    start_date_str = arguments.get("start_date")
    end_date_str = arguments.get("end_date")

    # Validate inputs
    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")

    # Parse optional date filters
    start_date = None
    end_date = None

    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as e:
            raise MCPError("INVALID_INPUT", f"Invalid start_date format: {e!s}") from e

    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as e:
            raise MCPError("INVALID_INPUT", f"Invalid end_date format: {e!s}") from e

    try:
        with get_session() as db:
            query = select(PlannedSession).where(PlannedSession.user_id == user_id)

            # Apply date filters if provided
            if start_date:
                query = query.where(PlannedSession.date >= start_date)
            if end_date:
                query = query.where(PlannedSession.date <= end_date)

            # Order by date (ascending)
            query = query.order_by(PlannedSession.date)

            sessions = db.execute(query).scalars().all()

            # Convert to dictionary format
            sessions_list = []
            for session in sessions:
                session_dict = {
                    "id": session.id,
                    "date": session.date.isoformat(),
                    "time": session.time,
                    "type": session.type,
                    "title": session.title,
                    "duration_minutes": session.duration_minutes,
                    "distance_km": session.distance_km,
                    "intensity": session.intensity,
                    "notes": session.notes,
                    "plan_type": session.plan_type,
                    "plan_id": session.plan_id,
                    "week_number": session.week_number,
                    "status": session.status,
                    "completed": session.completed,
                }
                sessions_list.append(session_dict)

            logger.info(
                "Retrieved planned sessions",
                user_id=user_id,
                count=len(sessions_list),
                start_date=start_date_str,
                end_date=end_date_str,
            )

            return {"sessions": sessions_list}

    except SQLAlchemyError as e:
        error_msg = f"Database error retrieving planned sessions: {e!s}"
        logger.error(error_msg, exc_info=True)
        raise MCPError("DB_ERROR", error_msg) from e
    except MCPError:
        raise
    except Exception as e:
        error_msg = f"Unexpected error retrieving planned sessions: {e!s}"
        logger.error(error_msg, exc_info=True)
        raise MCPError("INTERNAL_ERROR", error_msg) from e
