"""Background task to generate LLM feedback for execution summaries.

PHASE: Generate LLM feedback asynchronously after execution summary is created.
This ensures feedback generation doesn't block summary creation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.orm import Session

from app.api.schemas.schemas import ExecutionStateInfo
from app.db.models import Activity, PlannedSession
from app.db.session import get_session
from app.pairing.session_links import get_link_for_planned
from app.services.execution_feedback_service import generate_and_persist_feedback_async
from app.services.execution_state import derive_execution_state
from app.services.workout_execution_service import get_execution_summary


async def generate_feedback_for_activity(
    activity_id: str,
    planned_session_id: str | None = None,
    athlete_level: str = "intermediate",
) -> None:
    """Generate LLM feedback for an activity's execution summary (background task).

    This should be called asynchronously after execution summary is created.
    Does not block the main flow.

    Args:
        activity_id: Activity ID
        planned_session_id: Planned session ID if available
        athlete_level: Athlete level (low | intermediate | advanced)
    """
    try:
        with get_session() as session:
            # Get execution summary
            summary = get_execution_summary(session, activity_id)
            if not summary:
                logger.debug(f"No execution summary found for activity {activity_id}, skipping feedback generation")
                return

            # Check if feedback already exists
            if summary.llm_feedback:
                logger.debug(f"LLM feedback already exists for activity {activity_id}")
                return

            # Load activity and planned session
            activity = session.get(Activity, activity_id)
            if not activity:
                logger.warning(f"Activity {activity_id} not found for feedback generation")
                return

            planned = None
            if planned_session_id:
                planned = session.get(PlannedSession, planned_session_id)

            # Get session link
            session_link = None
            if planned and planned_session_id:
                session_link = get_link_for_planned(session, planned_session_id)
            linked_activity = activity if session_link else None

            # Compute execution state
            execution_state_info = ExecutionStateInfo(
                state=derive_execution_state(planned, linked_activity, datetime.now(timezone.utc)),
                reason=None,
                deltas=None,
                resolved_at=None,
            )

            # Generate and persist feedback
            await generate_and_persist_feedback_async(
                session,
                summary,
                execution_state_info,
                athlete_level,
            )

            logger.info(f"Generated LLM feedback for activity {activity_id}")

    except Exception as e:
        logger.warning(f"Failed to generate feedback for activity {activity_id}: {e}")
        # Don't raise - feedback generation is optional


def trigger_feedback_generation(
    activity_id: str,
    planned_session_id: str | None = None,
    athlete_level: str = "intermediate",
) -> None:
    """Trigger feedback generation in background (synchronous wrapper).

    This can be called from sync contexts. It runs the async function
    in a background task.

    Args:
        activity_id: Activity ID
        planned_session_id: Planned session ID if available
        athlete_level: Athlete level (low | intermediate | advanced)
    """
    try:
        # Run async function in background
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If loop is already running, create a task
            # Store task reference to prevent garbage collection
            background_task = asyncio.create_task(
                generate_feedback_for_activity(activity_id, planned_session_id, athlete_level)
            )
            # Keep reference alive - task handles its own errors
            _ = background_task
        else:
            # If no loop is running, run it
            asyncio.run(
                generate_feedback_for_activity(activity_id, planned_session_id, athlete_level)
            )
    except RuntimeError:
        # No event loop, create a new one
        asyncio.run(
            generate_feedback_for_activity(activity_id, planned_session_id, athlete_level)
        )
    except Exception as e:
        logger.warning(f"Failed to trigger feedback generation: {e}")
        # Don't raise - feedback generation is optional
