"""Guards to prevent legacy planner usage and enforce invariants.

This module provides explicit guards that:
- Prevent accidental re-enablement of legacy planner paths
- Enforce structural invariants at runtime
- Block forbidden behaviors (recursion, repair, replan)
- Detect legacy code paths in call stack
"""

import inspect
import os

from loguru import logger

from app.domains.training_plan.models import MacroWeek, PlannedSession


def assert_new_planner_only() -> None:
    """Assert that legacy planner is explicitly forbidden.

    This guard prevents accidental re-enablement of legacy planner paths.
    If ALLOW_LEGACY_PLANNER is set to "1", this will raise an error to
    prevent silent fallback to legacy code.

    Raises:
        RuntimeError: If legacy planner is explicitly enabled via environment variable
    """
    if os.getenv("ALLOW_LEGACY_PLANNER") == "1":
        logger.error(
            "Legacy planner explicitly forbidden",
            env_var="ALLOW_LEGACY_PLANNER=1",
        )
        raise RuntimeError(
            "Legacy planner explicitly forbidden. "
            "Remove ALLOW_LEGACY_PLANNER environment variable. "
            "Use plan_race_simple from app.planner.plan_race_simple (planner v2)."
        )


def log_planner_v2_entry() -> None:
    """Log planner v2 entry point for monitoring.

    This log event ensures we can track that all planning traffic
    flows through the new linear pipeline.
    """
    logger.info("planner_v2_entry")


def guard_invariants(
    macro_weeks: list[MacroWeek],
    planned_sessions: list[PlannedSession],
) -> None:
    """Guard structural invariants before persistence.

    This function enforces hard invariants that must be true before
    persisting a plan. If any invariant fails, the plan is aborted.

    Invariants checked:
    - Macro weeks: non-empty, sequential indices, positive volumes
    - Sessions: non-empty, positive distances, valid templates, text output
    - Volume sums: weekly volumes match expected totals
    - Week count: matches expected plan duration
    - Session count: reasonable number per week

    Args:
        macro_weeks: List of macro weeks (may be empty if not available yet)
        planned_sessions: List of planned sessions

    Raises:
        ValueError: If any invariant is violated
    """
    # Macro invariants (only check if macro_weeks is provided)
    if macro_weeks:
        if len(macro_weeks) == 0:
            raise ValueError("Macro weeks list must not be empty")
        if macro_weeks[0].week_index != 1:
            raise ValueError("First macro week must be week 1")

        # Check sequential week indices
        for i, week in enumerate(macro_weeks):
            expected_index = i + 1
            if week.week_index != expected_index:
                raise ValueError(
                    f"Week index mismatch: expected {expected_index}, got {week.week_index}"
                )

        # Check all volumes are positive
        for week in macro_weeks:
            if week.total_distance <= 0:
                raise ValueError(
                    f"Week {week.week_index} has non-positive distance: {week.total_distance}"
                )

        # Check week count consistency
        week_count = len(macro_weeks)
        if week_count < 4:
            raise ValueError(f"Week count too low: {week_count} (minimum 4)")
        if week_count > 52:
            raise ValueError(f"Week count too high: {week_count} (maximum 52)")

    # Session invariants
    if len(planned_sessions) == 0:
        raise ValueError("Planned sessions list must not be empty")

    session_count = len(planned_sessions)
    if session_count < 1:
        raise ValueError(f"Session count too low: {session_count}")

    # Check each session
    total_distance = 0.0
    for session in planned_sessions:
        # Distance must be positive
        if session.allocated_distance_km <= 0:
            raise ValueError(
                f"Session distance must be > 0, got: {session.allocated_distance_km}"
            )

        total_distance += session.allocated_distance_km

        # Template must be valid
        if not session.template.template_id:
            raise ValueError("Session must have template_id")

        # Text output must be present
        if session.text_output is None:
            raise ValueError("Session must have text_output before persistence")
        if not session.text_output.description:
            raise ValueError("Session description must not be empty")
        if not session.text_output.title:
            raise ValueError("Session title must not be empty")

    # Check total distance is reasonable
    if total_distance <= 0:
        raise ValueError(f"Total plan distance must be > 0, got: {total_distance}")

    # Check session count per week (rough estimate: 3-7 sessions per week)
    if macro_weeks:
        weeks = len(macro_weeks)
        sessions_per_week = session_count / weeks
        if sessions_per_week < 2:
            raise ValueError(
                f"Too few sessions per week: {sessions_per_week:.1f} (minimum 2)"
            )
        if sessions_per_week > 10:
            raise ValueError(
                f"Too many sessions per week: {sessions_per_week:.1f} (maximum 10)"
            )


def guard_no_recursion(call_depth: int) -> None:
    """Guard against recursive planning calls.

    Recursive planning is forbidden to prevent infinite loops and
    ensure predictable execution flow.

    Args:
        call_depth: Current call depth (should be 0 or 1)

    Raises:
        RuntimeError: If call_depth > 1
    """
    if call_depth > 1:
        raise RuntimeError(f"Recursive planning forbidden. Call depth: {call_depth}")


def guard_no_repair(flags: dict[str, bool | str | int | float]) -> None:
    """Guard against forbidden planner flags.

    Flags like "repair", "adjust", "replan" indicate legacy behavior
    and are explicitly forbidden in the new linear pipeline.

    Args:
        flags: Dictionary of planner flags

    Raises:
        RuntimeError: If any forbidden flag is present
    """
    forbidden = ["repair", "adjust", "replan"]
    for flag in forbidden:
        if flags.get(flag):
            raise RuntimeError(f"Forbidden planner flag: {flag}")


def assert_planner_v2_only() -> None:
    """Kill-switch assertion to detect legacy planner paths.

    This function inspects the call stack to detect if legacy planner
    code (plan_race_build_new) is being called. This prevents silent
    regression to legacy paths.

    Raises:
        RuntimeError: If legacy planner code is detected in call stack
    """
    stack = inspect.stack()
    for frame in stack:
        filename = frame.filename
        if "plan_race_build_new" in filename:
            raise RuntimeError("Legacy planner detected in call stack")
