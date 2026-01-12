"""Observability and metrics for planner pipeline (B10).

This module provides:
- Stage event logging (start/success/fail)
- Stage-level timing
- Metrics counters for success/failure rates
- Funnel tracking for debugging
"""

import time
from contextlib import contextmanager
from enum import StrEnum

from loguru import logger


class PlannerStage(StrEnum):
    """Canonical planner stage enum.

    Each stage represents a distinct phase in the planning pipeline.
    """

    MACRO = "macro_plan"
    PHILOSOPHY = "philosophy_select"
    STRUCTURE = "structure_load"
    VOLUME = "volume_allocate"
    TEMPLATE = "template_select"
    TEXT = "session_text"
    PERSIST = "calendar_persist"


def log_event(
    event: str,
    **kwargs: str | int | float | bool | None,
) -> None:
    """Log a structured event.

    This is a thin wrapper around logger.info that ensures consistent
    event logging format across the planner.

    Standard events:
    - macro_plan_generated: Macro plan created
    - week_skeleton_loaded: Week structure loaded
    - volume_allocated: Volume distributed across days
    - template_selected: Session templates selected
    - session_text_generated: Session descriptions generated
    - calendar_persisted: Plan saved to calendar

    Args:
        event: Event name (e.g., "planner_stage", "planner_macro_success")
        **kwargs: Additional structured fields to include in the log
    """
    logger.info(event, **kwargs)


def log_stage_event(
    stage: PlannerStage,
    status: str,
    plan_id: str | None = None,
    meta: dict[str, str | int | float | bool | None] | None = None,
) -> None:
    """Log a stage event (start/success/fail).

    This function emits structured logs for each planner stage, enabling:
    - Stage-level failure tracking
    - Funnel analysis
    - Debugging of specific stage failures

    Args:
        stage: Planner stage
        status: Event status ("start", "success", or "fail")
        plan_id: Optional plan identifier for correlation
        meta: Optional metadata dictionary to include in log

    Raises:
        ValueError: If status is not one of the allowed values
    """
    allowed_statuses = {"start", "success", "fail"}
    if status not in allowed_statuses:
        raise ValueError(f"Status must be one of {allowed_statuses}, got: {status}")

    log_data: dict[str, str | int | float | bool | None] = {
        "stage": stage.value,
        "status": status,
    }

    if plan_id:
        log_data["plan_id"] = plan_id

    if meta:
        log_data.update(meta)

    log_event("planner_stage", **log_data)


def log_stage_metric(stage: PlannerStage, success: bool) -> None:
    """Log a stage success/failure metric counter.

    These counters enable funnel analysis and alerting on failure rates.

    Args:
        stage: Planner stage
        success: True for success, False for failure
    """
    status = "success" if success else "failure"
    event_name = f"planner_{stage.value}_{status}"
    log_event(event_name)


@contextmanager
def timing(metric_name: str):
    """Context manager for timing operations.

    Emits timing metrics for performance monitoring and alerting.

    Args:
        metric_name: Metric name (e.g., "planner.stage.macro")

    Yields:
        None (context manager)
    """
    start_time = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start_time
        log_event(
            "planner_timing",
            metric=metric_name,
            duration_seconds=elapsed,
        )
