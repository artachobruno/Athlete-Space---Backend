"""Planning Invariant Observability.

This module provides logging for planning invariant failures.
Call this before re-raising PlanningInvariantError.
"""

from loguru import logger

from app.planning.errors import PlanningInvariantError


def log_planning_invariant_failure(err: PlanningInvariantError, context: dict[str, str | int | float | bool | None]) -> None:
    """Log a planning invariant failure with context.

    Args:
        err: The PlanningInvariantError that occurred
        context: Additional context dictionary for logging
    """
    logger.error(
        "PLANNING_INVARIANT_FAILED",
        extra={
            "code": err.code,
            "details": err.details,
            **context,
        },
    )
