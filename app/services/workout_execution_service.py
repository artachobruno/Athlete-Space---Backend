"""Workout execution summary service.

PHASE 5.2: Compute and store execution summaries to avoid repeated recomputation.

This service creates stable snapshots of execution outcomes that can be:
- Queried quickly in calendar APIs
- Used for coach narratives
- Analyzed for patterns
- Cached for performance
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.api.schemas.schemas import ExecutionStateInfo
from app.db.models import Activity, PlannedSession, WorkoutExecutionSummary
from app.pairing.delta_computation import compute_link_deltas
from app.pairing.session_links import get_link_for_planned
from app.services.execution_feedback_service import generate_execution_feedback
from app.services.execution_state import derive_execution_state
from app.workouts.compliance_service import ComplianceService
from app.workouts.execution_models import StepCompliance, WorkoutComplianceSummary, WorkoutExecution
from app.workouts.models import Workout


def compute_execution_summary(
    session: Session,
    planned_session_id: str | None = None,
    activity_id: str | None = None,
    user_id: str | None = None,
) -> WorkoutExecutionSummary | None:
    """Compute execution summary for a planned session and activity.

    PHASE 5.2: Creates a stable snapshot of execution outcome.

    Args:
        session: Database session
        planned_session_id: Planned session ID (optional)
        activity_id: Activity ID (required)
        user_id: User ID (optional, inferred if not provided)

    Returns:
        WorkoutExecutionSummary if computation succeeds, None otherwise
    """
    if not activity_id:
        logger.warning("Cannot compute execution summary without activity_id")
        return None

    # Load activity
    activity = session.get(Activity, activity_id)
    if not activity:
        logger.warning(f"Activity {activity_id} not found for execution summary")
        return None

    if not user_id:
        user_id = activity.user_id

    # Load planned session if provided
    planned_session = None
    if planned_session_id:
        planned_session = session.get(PlannedSession, planned_session_id)
        if not planned_session:
            logger.warning(f"Planned session {planned_session_id} not found for execution summary")

    # Get session link
    session_link = None
    if planned_session and planned_session_id:
        session_link = get_link_for_planned(session, planned_session_id)

    # Compute execution state
    linked_activity = activity if session_link else None
    execution_state = derive_execution_state(planned_session, linked_activity, datetime.now(timezone.utc))

    # Compute deltas
    deltas = None
    if planned_session and activity:
        deltas = compute_link_deltas(planned_session, activity)

    # Compute compliance score from workout execution if available
    compliance_score = None
    step_comparison = None

    if planned_session and planned_session.workout_id:
        try:
            # Get workout execution
            execution = session.execute(
                select(WorkoutExecution).where(
                    WorkoutExecution.workout_id == planned_session.workout_id,
                    WorkoutExecution.activity_id == activity_id,
                )
            ).scalar_one_or_none()

            if execution:
                # Get compliance summary
                compliance_summary = session.execute(
                    select(WorkoutComplianceSummary).where(
                        WorkoutComplianceSummary.workout_id == planned_session.workout_id
                    )
                ).scalar_one_or_none()

                if compliance_summary:
                    compliance_score = compliance_summary.overall_compliance_pct

                    # Get step compliance records for step_comparison
                    step_compliance_records = session.execute(
                        select(StepCompliance).where(
                            StepCompliance.workout_execution_id == execution.id
                        ).order_by(StepCompliance.step_order)
                    ).scalars().all()

                    if step_compliance_records:
                        step_comparison = [
                            {
                                "step_order": sc.step_order,
                                "compliance_pct": sc.compliance_pct,
                                "time_in_range_seconds": sc.time_in_range_seconds,
                                "overshoot_seconds": sc.overshoot_seconds,
                                "undershoot_seconds": sc.undershoot_seconds,
                            }
                            for sc in step_compliance_records
                        ]
        except Exception as e:
            logger.warning(f"Failed to compute compliance for execution summary: {e}")

    # Generate narrative
    narrative = _generate_narrative(execution_state, deltas, compliance_score)

    # Generate LLM feedback if execution state allows it
    # This is async, so we'll handle it in ensure_execution_summary
    # For now, we'll generate it synchronously in persist_execution_summary

    # Create and return WorkoutExecutionSummary model instance
    return WorkoutExecutionSummary(
        activity_id=activity_id,
        planned_session_id=planned_session_id,
        user_id=user_id or "",
        compliance_score=compliance_score,
        step_comparison=step_comparison,
        narrative=narrative,
        computed_at=datetime.now(timezone.utc),
    )


def _generate_narrative(
    execution_state: str,
    deltas: dict[str, float | int | None] | None,
    compliance_score: float | None,
) -> str:
    """Generate human-readable narrative for execution summary.

    Args:
        execution_state: Execution state (unexecuted, executed_as_planned, executed_unplanned, missed)
        deltas: Delta values between planned and actual
        compliance_score: Compliance score (0.0-1.0) if available

    Returns:
        Narrative string
    """
    if execution_state == "executed_as_planned":
        if compliance_score is not None:
            if compliance_score >= 0.9:
                return "Executed as planned with excellent compliance"
            if compliance_score >= 0.7:
                return "Executed as planned with good compliance"
            return "Executed as planned with moderate compliance"
        if deltas:
            duration_delta = deltas.get("duration_seconds", 0) or 0
            if abs(duration_delta) < 60:
                return "Executed as planned, duration matched closely"
            if duration_delta > 0:
                return f"Executed as planned, {int(duration_delta // 60)} minutes longer than planned"
            return f"Executed as planned, {int(abs(duration_delta) // 60)} minutes shorter than planned"
        return "Executed as planned"
    if execution_state == "executed_unplanned":
        return "Completed without a planned session"
    if execution_state == "missed":
        return "Planned session was not completed"
    if execution_state == "unexecuted":
        return "Session not yet executed"
    return "Execution status unknown"


async def generate_and_persist_feedback_async(
    session: Session,
    summary: WorkoutExecutionSummary,
    execution_state_info: ExecutionStateInfo,
    athlete_level: str = "intermediate",
) -> None:
    """Generate and persist LLM feedback for execution summary (async).

    This should be called from background tasks or async endpoints.
    Does not block summary creation.

    Args:
        session: Database session
        summary: WorkoutExecutionSummary to add feedback to
        execution_state_info: ExecutionStateInfo for feedback generation
        athlete_level: Athlete level (low | intermediate | advanced)
    """
    # Check if feedback already exists (safely handle missing column)
    try:
        if summary.llm_feedback:
            return
    except (AttributeError, KeyError):
        # Column may not exist in database (migration not run)
        logger.debug("llm_feedback column not available, will generate new feedback")

    # Generate feedback
    feedback = await generate_execution_feedback(
        execution_state_info,
        summary,
        athlete_level,
    )

    if feedback:
        # Store as JSON dict
        summary.llm_feedback = {
            "text": feedback.text,
            "tone": feedback.tone,
            "generated_at": feedback.generated_at,
        }
        session.commit()
        logger.debug(f"Generated and persisted LLM feedback for activity {summary.activity_id}")


def persist_execution_summary(
    session: Session,
    summary: WorkoutExecutionSummary,
) -> None:
    """Persist execution summary to database.

    PHASE 5.2: Stores summary in workout_execution_summaries table.

    Args:
        session: Database session
        summary: WorkoutExecutionSummary to persist
    """
    try:
        # Check if summary already exists
        existing = session.execute(
            select(WorkoutExecutionSummary).where(
                WorkoutExecutionSummary.activity_id == summary.activity_id
            )
        ).scalar_one_or_none()

        if existing:
            # Update existing
            existing.planned_session_id = summary.planned_session_id
            existing.user_id = summary.user_id
            existing.compliance_score = summary.compliance_score
            existing.step_comparison = summary.step_comparison
            existing.narrative = summary.narrative
            existing.computed_at = summary.computed_at
            existing.updated_at = datetime.now(timezone.utc)
            logger.debug(
                "Updated execution summary",
                activity_id=summary.activity_id,
                planned_session_id=summary.planned_session_id,
            )
        else:
            # Add new
            session.add(summary)
            logger.debug(
                "Created execution summary",
                activity_id=summary.activity_id,
                planned_session_id=summary.planned_session_id,
            )
    except Exception as e:
        logger.warning(f"Failed to persist execution summary: {e}")
        # Don't raise - execution summary is optional


def get_execution_summary(
    session: Session,
    activity_id: str,
) -> WorkoutExecutionSummary | None:
    """Get execution summary for an activity.

    Args:
        session: Database session
        activity_id: Activity ID

    Returns:
        WorkoutExecutionSummary if found, None otherwise
    """
    try:
        return session.execute(
            select(WorkoutExecutionSummary).where(
                WorkoutExecutionSummary.activity_id == activity_id
            )
        ).scalar_one_or_none()
    except ProgrammingError as e:
        # Handle missing column gracefully (migration may not have run yet)
        error_str = str(e).lower()
        if "llm_feedback" in error_str or "undefinedcolumn" in error_str or "does not exist" in error_str:
            logger.warning(
                "workout_execution_summaries.llm_feedback column missing - migration may not have run. "
                "Returning None to avoid transaction abort. Run migration: migrate_add_llm_feedback_to_execution_summaries"
            )
            # Rollback the transaction to avoid cascading errors
            session.rollback()
            return None
        # Re-raise if it's a different ProgrammingError
        raise
    except Exception as e:
        logger.debug(f"Execution summary not found or table doesn't exist: {e}")
        return None


async def ensure_execution_summary_async(
    session: Session,
    planned_session_id: str | None = None,
    activity_id: str | None = None,
    user_id: str | None = None,
    force_recompute: bool = False,
    athlete_level: str = "intermediate",
) -> WorkoutExecutionSummary | None:
    """Ensure execution summary exists, computing if needed.

    PHASE 5.2: Main entry point for execution summary management.

    Args:
        session: Database session
        planned_session_id: Planned session ID (optional)
        activity_id: Activity ID (optional)
        user_id: User ID (optional)
        force_recompute: Force recomputation even if summary exists
        athlete_level: Athlete level for feedback generation
        activity_id: Activity ID (required)
        user_id: User ID (optional)
        force_recompute: If True, recompute even if summary exists

    Returns:
        WorkoutExecutionSummary if computation succeeds, None otherwise
    """
    if not activity_id:
        return None

    # Check if summary already exists
    if not force_recompute:
        existing = get_execution_summary(session, activity_id)
        if existing:
            return existing

    # Compute new summary
    summary = compute_execution_summary(
        session=session,
        planned_session_id=planned_session_id,
        activity_id=activity_id,
        user_id=user_id,
    )

    if summary:
        # Persist to database
        persist_execution_summary(session, summary)

        # Generate LLM feedback if needed (async, non-blocking)
        try:
            # Compute execution state for feedback
            activity = session.get(Activity, activity_id) if activity_id else None
            planned = session.get(PlannedSession, planned_session_id) if planned_session_id else None
            session_link = get_link_for_planned(session, planned_session_id) if planned_session_id else None
            linked_activity = activity if session_link else None

            execution_state_info = ExecutionStateInfo(
                state=derive_execution_state(planned, linked_activity, datetime.now(timezone.utc)),
                reason=None,
                deltas=None,
                resolved_at=None,
            )

            await generate_and_persist_feedback_async(
                session,
                summary,
                execution_state_info,
                athlete_level,
            )
        except Exception as e:
            logger.warning(f"Failed to generate LLM feedback (non-critical): {e}")
            # Don't fail summary creation if feedback generation fails

    return summary


def ensure_execution_summary(
    session: Session,
    planned_session_id: str | None = None,
    activity_id: str | None = None,
    user_id: str | None = None,
    force_recompute: bool = False,
) -> WorkoutExecutionSummary | None:
    """Synchronous wrapper for ensure_execution_summary_async.

    For backward compatibility, this skips LLM feedback generation.
    Use ensure_execution_summary_async for full functionality.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(
        ensure_execution_summary_async(
            session=session,
            planned_session_id=planned_session_id,
            activity_id=activity_id,
            user_id=user_id,
            force_recompute=force_recompute,
        )
    )
