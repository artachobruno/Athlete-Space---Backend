"""Execution Metrics - Phase 6A.

Observability and metrics for plan execution.
Logs execution outcomes for monitoring and analysis.
"""

from loguru import logger


def log_execution_metrics(
    *,
    user_id: str,
    plan_id: str,
    week_index: int,
    sessions_written: int,
    conflicts_detected: int,
    execution_status: str,
) -> None:
    """Log execution metrics.

    Metrics:
    - sessions_written: Number of sessions written
    - conflicts_detected: Number of conflicts detected
    - weeks_successfully_executed: Weeks executed successfully (1 if SUCCESS, 0 otherwise)
    - weeks_blocked_by_conflict: Weeks blocked by conflicts (1 if BLOCKED, 0 otherwise)

    Args:
        user_id: User ID
        plan_id: Plan identifier
        week_index: Week index
        sessions_written: Number of sessions written
        conflicts_detected: Number of conflicts detected
        execution_status: Execution status (SUCCESS, BLOCKED, ERROR)
    """
    logger.info(
        "[EXECUTION] Execution metrics",
        user_id=user_id,
        plan_id=plan_id,
        week_index=week_index,
        sessions_written=sessions_written,
        conflicts_detected=conflicts_detected,
        execution_status=execution_status,
        weeks_successfully_executed=1 if execution_status == "SUCCESS" else 0,
        weeks_blocked_by_conflict=1 if execution_status == "BLOCKED" else 0,
    )


def log_week_write_started(
    user_id: str,
    plan_id: str,
    sessions_count: int,
    dry_run: bool,
) -> None:
    """Log week write started.

    Args:
        user_id: User ID
        plan_id: Plan identifier
        sessions_count: Number of sessions to write
        dry_run: Whether this is a dry run
    """
    logger.info(
        "[EXECUTION] Week write started",
        user_id=user_id,
        plan_id=plan_id,
        sessions_count=sessions_count,
        dry_run=dry_run,
    )


def log_conflict_detected(
    user_id: str,
    plan_id: str,
    conflicts_count: int,
    dry_run: bool,
) -> None:
    """Log conflict detected.

    Args:
        user_id: User ID
        plan_id: Plan identifier
        conflicts_count: Number of conflicts detected
        dry_run: Whether this was a dry run
    """
    logger.warning(
        "[EXECUTION] Conflict detected",
        user_id=user_id,
        plan_id=plan_id,
        conflicts_count=conflicts_count,
        dry_run=dry_run,
    )


def log_week_write_committed(
    user_id: str,
    plan_id: str,
    sessions_written: int,
    sessions_total: int,
) -> None:
    """Log week write committed.

    Args:
        user_id: User ID
        plan_id: Plan identifier
        sessions_written: Number of sessions written
        sessions_total: Total number of sessions
    """
    logger.info(
        "[EXECUTION] Week write committed",
        user_id=user_id,
        plan_id=plan_id,
        sessions_written=sessions_written,
        sessions_total=sessions_total,
    )


def log_week_write_rolled_back(
    user_id: str,
    plan_id: str,
    error: str,
) -> None:
    """Log week write rolled back.

    Args:
        user_id: User ID
        plan_id: Plan identifier
        error: Error message
    """
    logger.error(
        "[EXECUTION] Week write rolled back",
        user_id=user_id,
        plan_id=plan_id,
        error=error,
    )


def log_manual_override_rate(
    user_id: str,
    plan_id: str,
    manual_overrides: int,
    total_sessions: int,
) -> None:
    """Log manual override rate.

    Args:
        user_id: User ID
        plan_id: Plan identifier
        manual_overrides: Number of manual overrides
        total_sessions: Total number of sessions
    """
    rate = (manual_overrides / total_sessions * 100) if total_sessions > 0 else 0.0
    logger.info(
        "[EXECUTION] Manual override rate",
        user_id=user_id,
        plan_id=plan_id,
        manual_overrides=manual_overrides,
        total_sessions=total_sessions,
        manual_override_rate=round(rate, 2),
    )
