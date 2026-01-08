"""Coach analysis tools for MCP DB server.

These tools handle coach-specific operations that require athlete state.
"""

import asyncio
import sys
from pathlib import Path

from loguru import logger

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.coach.schemas.athlete_state import AthleteState
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_week import plan_week
from app.coach.tools.run_analysis import run_analysis
from app.coach.tools.share_report import share_report
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
        return {"message": result}
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error planning week: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to plan week: {e!s}") from e


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
        return {"message": result}
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error running analysis: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to run analysis: {e!s}") from e


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
        return {"message": result}
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error explaining training state: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to explain training state: {e!s}") from e


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
        return {"message": result}
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error adjusting training load: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to adjust training load: {e!s}") from e


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
        return {"message": result}
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error recommending next session: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to recommend next session: {e!s}") from e


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
        return {"message": result}
    except MCPError:
        raise
    except Exception as e:
        logger.error(f"Error generating report: {e}", exc_info=True)
        raise MCPError("INTERNAL_ERROR", f"Failed to generate report: {e!s}") from e

