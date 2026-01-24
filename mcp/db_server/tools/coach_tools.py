"""Coach analysis tools for MCP DB server.

These tools handle coach-specific operations that require athlete state.
"""

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.calendar.training_summary import build_training_summary
from app.coach.executor.errors import PersistenceError
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.constraints import TrainingConstraints
from app.coach.tools.adjust_load import adjust_training_load
from app.coach.tools.explain_state import explain_training_state
from app.coach.tools.next_session import recommend_next_session
from app.coach.tools.plan_week import plan_week
from app.coach.tools.run_analysis import run_analysis
from app.coach.tools.share_report import share_report
from app.coach.utils.constraints import RecoveryState
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


async def plan_week_tool_impl(
    state: AthleteState,
    user_id: str | None,
    athlete_id: int | None,
    user_feedback: str | None,
) -> str:
    """Async wrapper for plan_week."""
    return await plan_week(state, user_id, athlete_id, user_feedback)


def plan_week_tool(arguments: dict) -> dict:
    """Generate a 7-day training plan for the current week.

    Contract: plan_week.json

    Args:
        arguments: Dictionary containing:
            - state: AthleteState dict (required)
            - user_id: User ID (required)
            - athlete_id: Athlete ID (required)
            - user_feedback: Optional user feedback for constraint generation

    Returns:
        Success message with plan details

    Raises:
        MCPError: If inputs are invalid or planning fails
    """
    state_dict = arguments.get("state")
    user_id = arguments.get("user_id")
    athlete_id = arguments.get("athlete_id")
    user_feedback = arguments.get("user_feedback")

    if not state_dict or not isinstance(state_dict, dict):
        raise MCPError("INVALID_INPUT", "Missing or invalid state")

    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")

    if athlete_id is None:
        raise MCPError("INVALID_INPUT", "Missing athlete_id")

    try:
        logger.info(
            "MCP plan_week_tool: Starting plan generation",
            user_id=user_id,
            athlete_id=athlete_id,
            has_feedback=user_feedback is not None,
        )
        state = _parse_athlete_state(state_dict)
        result = asyncio.run(plan_week_tool_impl(state, user_id, athlete_id, user_feedback))
        logger.info(
            "MCP plan_week_tool: Plan generation completed",
            user_id=user_id,
            athlete_id=athlete_id,
            result_length=len(result) if result else 0,
            result_preview=result[:200] if result else None,
        )
    except MCPError:
        raise
    except PersistenceError as e:
        logger.error(
            "MCP plan_week_tool: Persistence failed",
            user_id=user_id,
            athlete_id=athlete_id,
            error=str(e),
        )
        raise MCPError("CALENDAR_PERSISTENCE_FAILED", "calendar_persistence_failed") from e
    except Exception as e:
        logger.error(
            "MCP plan_week_tool: Error planning week",
            user_id=user_id,
            athlete_id=athlete_id,
            error=str(e),
            exc_info=True,
        )
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
    """Adjust training load based on constraints and training state.

    Contract: adjust_training_load.json

    Args:
        arguments: Dictionary containing:
            - user_id: User ID (required)
            - athlete_id: Optional athlete ID
            - constraints: Optional training constraints dict (B17 format)
            - window_days: Optional window days for training summary (default: 14)

    Returns:
        LoadAdjustmentDecision as dict

    B18: Training Load Adjustment Tool
    Safely applies training load changes using bounded, auditable, deterministic rules.

    Args:
        user_id: User ID (required)
        athlete_id: Optional athlete ID (will be resolved from user_id if not provided)
        constraints: Optional training constraints dict (B17 format)
        window_days: Optional window days for training summary (default: 14)

    Returns:
        LoadAdjustmentDecision as dict
    """
    user_id = arguments.get("user_id")
    athlete_id = arguments.get("athlete_id")
    constraints_dict = arguments.get("constraints")
    window_days = arguments.get("window_days", 14)

    if not user_id or not isinstance(user_id, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_id")

    try:
        # Build TrainingSummary (B16)
        training_summary = build_training_summary(
            user_id=user_id,
            athlete_id=athlete_id,
            window_days=window_days,
        )

        # Build RecoveryState (B19) from TrainingSummary
        load_metrics = training_summary.load
        atl_raw = load_metrics.get("atl", 0.0)
        tsb_raw = load_metrics.get("tsb", 0.0)
        ctl_raw = load_metrics.get("ctl", 0.0)

        # Convert to float (handle case where dict values might be str)
        atl = float(atl_raw) if isinstance(atl_raw, (int, float)) else 0.0
        tsb = float(tsb_raw) if isinstance(tsb_raw, (int, float)) else 0.0
        ctl = float(ctl_raw) if isinstance(ctl_raw, (int, float)) else 0.0

        # Determine recovery status from TSB
        if tsb < -25.0:
            recovery_status: Literal["under", "adequate", "over"] = "over"
        elif tsb > 5.0:
            recovery_status = "under"
        else:
            recovery_status = "adequate"

        # Collect risk flags from training summary
        risk_flags: list[str] = []
        if atl > 0 and ctl > 0 and atl / ctl > 1.5:
            risk_flags.append("ATL_SPIKE")
        if tsb < -25.0:
            risk_flags.append("TSB_LOW")
        if training_summary.reliability_flags.high_variance:
            risk_flags.append("HIGH_VARIANCE")

        recovery_state = RecoveryState(
            atl=atl,
            tsb=tsb,
            recovery_status=recovery_status,
            risk_flags=risk_flags,
        )

        # Parse constraints if provided
        constraints: TrainingConstraints | None = None
        if constraints_dict and isinstance(constraints_dict, dict):
            try:
                constraints = TrainingConstraints(**constraints_dict)
            except Exception as e:
                logger.warning(f"Failed to parse constraints, using defaults: {e}")
                constraints = None

        # Call B18 adjustment tool
        decision = adjust_training_load(
            training_summary=training_summary,
            recovery_state=recovery_state,
            constraints=constraints,
        )

        # Return decision as dict
        return decision.model_dump()

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
                query = query.where(PlannedSession.starts_at >= start_date)
            if end_date:
                query = query.where(PlannedSession.starts_at <= end_date)

            # Order by starts_at (ascending)
            query = query.order_by(PlannedSession.starts_at)

            sessions = db.execute(query).scalars().all()

            # Convert to dictionary format
            sessions_list = []
            for session in sessions:
                # Convert duration_seconds to minutes if present
                duration_minutes = None
                if session.duration_seconds is not None:
                    duration_minutes = int(session.duration_seconds / 60)

                # Convert distance_meters to km if present
                distance_km = None
                if session.distance_meters is not None:
                    distance_km = float(session.distance_meters / 1000.0)

                # Extract date and time from starts_at
                starts_at_iso = session.starts_at.isoformat()
                starts_at_date = session.starts_at.date().isoformat()
                starts_at_time = session.starts_at.time().isoformat()

                session_dict = {
                    "id": str(session.id),  # Convert UUID to string for JSON serialization
                    "date": starts_at_date,
                    "time": starts_at_time,
                    "starts_at": starts_at_iso,
                    "ends_at": session.ends_at.isoformat() if session.ends_at else None,
                    "type": session.session_type,
                    "session_type": session.session_type,
                    "sport": session.sport,
                    "title": session.title,
                    "duration_seconds": session.duration_seconds,
                    "duration_minutes": duration_minutes,
                    "distance_meters": session.distance_meters,
                    "distance_km": distance_km,
                    "intensity": session.intensity,
                    "intent": session.intent,
                    "notes": session.notes,
                    "status": session.status,
                    "season_plan_id": str(session.season_plan_id) if session.season_plan_id else None,  # Convert UUID to string
                    "revision_id": str(session.revision_id) if session.revision_id else None,  # Convert UUID to string
                    "workout_id": str(session.workout_id) if session.workout_id else None,  # Convert UUID to string
                    "tags": session.tags if session.tags else [],
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
