"""B8.5 — Progress emission (UI + logs).

This module emits progress events after each step for UI tracking
and debugging. Progress is emitted as structured logs and can be
extended to emit to external systems (e.g., WebSocket, event bus).
"""

import time
from dataclasses import asdict

from loguru import logger

from app.orchestrator.planner_v2.state import PlannerV2State


def emit_planning_progress(
    plan_id: str,
    step: str,
    status: str,
    *,
    percent: int | None = None,
    summary: dict[str, object] | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Emit planning progress event.

    This function logs progress events in structured format.
    Can be extended to emit to WebSocket, event bus, etc.

    Args:
        plan_id: Plan identifier
        step: Step name (e.g., "macro_plan", "templates")
        status: Status ("completed", "failed", "in_progress")
        percent: Progress percentage (0-100)
        summary: Optional summary dictionary with step-specific data
        error: Optional error message (if status is "failed")
        duration_ms: Optional step duration in milliseconds
    """
    event: dict[str, object] = {
        "plan_id": plan_id,
        "step": step,
        "status": status,
    }

    if percent is not None:
        event["percent"] = percent

    if summary:
        event["summary"] = summary

    if error:
        event["error"] = error

    if duration_ms is not None:
        event["duration_ms"] = duration_ms

    logger.info("planning_progress", **event)


def emit_step_start(plan_id: str, step: str, percent: int) -> float:
    """Emit step start event and return start time.

    Args:
        plan_id: Plan identifier
        step: Step name
        percent: Progress percentage

    Returns:
        Start time (monotonic) for duration calculation
    """
    emit_planning_progress(
        plan_id=plan_id,
        step=step,
        status="in_progress",
        percent=percent,
    )
    return time.monotonic()


def emit_step_complete(
    plan_id: str,
    step: str,
    percent: int,
    start_time: float,
    summary: dict[str, object] | None = None,
) -> None:
    """Emit step completion event.

    Args:
        plan_id: Plan identifier
        step: Step name
        percent: Progress percentage
        start_time: Start time from emit_step_start()
        summary: Optional summary dictionary
    """
    duration_ms = int((time.monotonic() - start_time) * 1000)

    emit_planning_progress(
        plan_id=plan_id,
        step=step,
        status="completed",
        percent=percent,
        summary=summary,
        duration_ms=duration_ms,
    )


def emit_step_failed(
    plan_id: str,
    step: str,
    start_time: float | None,
    error: str,
) -> None:
    """Emit step failure event.

    Args:
        plan_id: Plan identifier
        step: Step name
        start_time: Optional start time for duration calculation
        error: Error message
    """
    duration_ms = None
    if start_time is not None:
        duration_ms = int((time.monotonic() - start_time) * 1000)

    emit_planning_progress(
        plan_id=plan_id,
        step=step,
        status="failed",
        error=error,
        duration_ms=duration_ms,
    )


def emit_plan_summary(state: PlannerV2State) -> None:
    """Emit final plan summary after completion.

    Args:
        state: Final planner state
    """
    if state.text_weeks is None:
        return

    total_weeks = len(state.text_weeks)
    total_sessions = sum(len(week.sessions) for week in state.text_weeks)

    summary = {
        "plan_id": state.plan_id,
        "total_weeks": total_weeks,
        "philosophy": state.philosophy_id,
        "sessions_created": total_sessions,
    }

    if state.persist_result:
        summary["sessions_persisted"] = state.persist_result.created
        summary["sessions_updated"] = state.persist_result.updated

    logger.info("plan_summary", **summary)
