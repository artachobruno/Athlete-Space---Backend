"""Execution Orchestrator - Phase 6A.

Bridge Phase 5 output (WeekPlan) → calendar write safely.
No plan mutation - plans are immutable once materialized.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import NAMESPACE_DNS, uuid5

from loguru import logger

from app.calendar.write_service import CalendarWriteService
from app.planning.execution.contracts import ExecutableSession, ExecutionSource
from app.planning.output.models import Day, MaterializedSession, WeekPlan


@dataclass(frozen=True)
class ExecutionResult:
    """Result of executing a week plan.

    Attributes:
        status: Execution status (SUCCESS, BLOCKED, ERROR)
        sessions_written: Number of sessions written
        conflicts_detected: List of conflicts (if blocked)
        error: Error message (if error)
    """

    status: Literal["SUCCESS", "BLOCKED", "ERROR"]
    sessions_written: int
    conflicts_detected: list  # list[CalendarConflict] but avoiding circular import
    error: str | None = None


def _day_name_to_weekday_offset(day: Day) -> int:
    """Convert day name to weekday offset (Monday = 0).

    Args:
        day: Day name (mon, tue, wed, thu, fri, sat, sun)

    Returns:
        Weekday offset (0-6, Monday = 0)
    """
    day_map: dict[Day, int] = {
        "mon": 0,
        "tue": 1,
        "wed": 2,
        "thu": 3,
        "fri": 4,
        "sat": 5,
        "sun": 6,
    }
    return day_map[day]


def _week_start_date(start_date: date, week_index: int) -> date:
    """Calculate the start date (Monday) of a week.

    Args:
        start_date: Plan start date
        week_index: Zero-based week index

    Returns:
        Monday date of the week
    """
    # Calculate days from start_date to the Monday of week_index
    # First, find the Monday of the week containing start_date
    days_to_monday = (start_date.weekday()) % 7  # Monday = 0
    first_monday = start_date - timedelta(days=days_to_monday)

    # Add weeks
    return first_monday + timedelta(weeks=week_index)


def _day_to_date(start_date: date, week_index: int, day: Day) -> date:
    """Convert day name and week_index to actual date.

    Args:
        start_date: Plan start date
        week_index: Zero-based week index
        day: Day name (mon, tue, wed, etc.)

    Returns:
        Actual calendar date
    """
    week_start = _week_start_date(start_date, week_index)
    day_offset = _day_name_to_weekday_offset(day)
    return week_start + timedelta(days=day_offset)


def _week_plan_to_executable_sessions(
    week_plan: WeekPlan,
    plan_id: str,
    start_date: date,
    source: ExecutionSource = "ai_plan",
) -> list[ExecutableSession]:
    """Convert WeekPlan to list of ExecutableSession.

    Phase 6A: Pure conversion - no mutation, no validation.

    Args:
        week_plan: WeekPlan from Phase 5
        plan_id: Plan identifier
        start_date: Plan start date (for date computation)
        source: Source of sessions (default: "ai_plan")

    Returns:
        List of ExecutableSession objects
    """
    executable_sessions: list[ExecutableSession] = []

    for session in week_plan.sessions:
        # Skip rest days
        if session.session_type == "rest":
            continue

        # Calculate actual date from week_index + day
        session_date = _day_to_date(start_date, week_plan.week_index, session.day)

        # Generate stable UUID for session_id
        # Use deterministic ID based on plan_id, week_index, and day
        session_id = str(
            uuid5(
                NAMESPACE_DNS,
                f"{plan_id}:week{week_plan.week_index}:{session.day}:{session.session_template_id}",
            )
        )

        executable = ExecutableSession(
            session_id=session_id,
            plan_id=plan_id,
            week_index=week_plan.week_index,
            date=session_date,
            duration_minutes=session.duration_minutes,
            distance_miles=session.distance_miles,
            session_type=session.session_type,
            session_template_id=session.session_template_id,
            source=source,
        )

        executable_sessions.append(executable)

    return executable_sessions


def execute_week_plan(
    user_id: str,
    plan_id: str,
    week_plan: WeekPlan,
    start_date: date,
    *,
    allow_conflicts: bool = False,
) -> ExecutionResult:
    """Execute a week plan by writing sessions to the calendar.

    Phase 6A: Bridge Phase 5 output → calendar write safely.

    Flow:
    1. WeekPlan → ExecutableSession[]
    2. detect_conflicts()
    3. if conflicts and not allow_conflicts: return BLOCKED
    4. write_week()
    5. return SUCCESS

    Args:
        user_id: User ID
        plan_id: Plan identifier
        week_plan: WeekPlan from Phase 5 (immutable)
        start_date: Plan start date (for date computation)
        allow_conflicts: If True, write even with conflicts (default: False)

    Returns:
        ExecutionResult with status and details
    """
    logger.info(
        "[EXECUTION] Week plan execution started",
        user_id=user_id,
        plan_id=plan_id,
        week_index=week_plan.week_index,
        sessions_count=len(week_plan.sessions),
    )

    try:
        # Convert WeekPlan to ExecutableSession[]
        executable_sessions = _week_plan_to_executable_sessions(
            week_plan=week_plan,
            plan_id=plan_id,
            start_date=start_date,
            source="ai_plan",
        )

        if not executable_sessions:
            logger.warning(
                "[EXECUTION] No executable sessions (all rest days?)",
                user_id=user_id,
                plan_id=plan_id,
                week_index=week_plan.week_index,
            )
            return ExecutionResult(
                status="SUCCESS",
                sessions_written=0,
                conflicts_detected=[],
                error=None,
            )

        # Write to calendar
        write_service = CalendarWriteService()
        write_result = write_service.write_week(
            user_id=user_id,
            plan_id=plan_id,
            sessions=executable_sessions,
            dry_run=False,
        )

        # Check for conflicts
        if write_result.conflicts_detected:
            if allow_conflicts:
                logger.warning(
                    "[EXECUTION] Conflicts detected but allow_conflicts=True, continuing",
                    user_id=user_id,
                    plan_id=plan_id,
                    conflicts_count=len(write_result.conflicts_detected),
                )
                # Still return BLOCKED even if allow_conflicts - conflicts prevent write
                return ExecutionResult(
                    status="BLOCKED",
                    sessions_written=0,
                    conflicts_detected=write_result.conflicts_detected,
                    error=f"Conflicts detected: {len(write_result.conflicts_detected)} conflicts",
                )
            logger.warning(
                "[EXECUTION] Week write blocked by conflicts",
                user_id=user_id,
                plan_id=plan_id,
                conflicts_count=len(write_result.conflicts_detected),
            )
            return ExecutionResult(
                status="BLOCKED",
                sessions_written=0,
                conflicts_detected=write_result.conflicts_detected,
                error=f"Conflicts detected: {len(write_result.conflicts_detected)} conflicts",
            )

        logger.info(
            "[EXECUTION] Week plan execution completed",
            user_id=user_id,
            plan_id=plan_id,
            week_index=week_plan.week_index,
            sessions_written=write_result.sessions_written,
        )

        return ExecutionResult(
            status="SUCCESS",
            sessions_written=write_result.sessions_written,
            conflicts_detected=[],
            error=None,
        )

    except Exception as e:
        logger.error(
            "[EXECUTION] Week plan execution failed",
            user_id=user_id,
            plan_id=plan_id,
            week_index=week_plan.week_index,
            error=str(e),
            exc_info=True,
        )
        return ExecutionResult(
            status="ERROR",
            sessions_written=0,
            conflicts_detected=[],
            error=str(e),
        )
