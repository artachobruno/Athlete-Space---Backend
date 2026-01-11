"""Execution Guards - Phase 6A.

Hard safety guards that prevent unsafe calendar writes.
All failures must raise typed exceptions.
"""

from app.planning.execution.contracts import ExecutableSession


class ExecutionGuardError(Exception):
    """Base exception for execution guard violations."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class MissingPlanIdError(ExecutionGuardError):
    """Raised when attempting to write sessions without plan_id."""

    def __init__(self):
        super().__init__("Cannot write sessions without plan_id")


class MissingSessionTemplateIdError(ExecutionGuardError):
    """Raised when attempting to write sessions without session_template_id."""

    def __init__(self, session_id: str):
        super().__init__(f"Session {session_id} missing session_template_id")


class InvalidSessionSourceError(ExecutionGuardError):
    """Raised when session source is not from Phase 5."""

    def __init__(self, session_id: str, source: str):
        super().__init__(f"Session {session_id} has invalid source for execution: {source}")


class InvalidDurationError(ExecutionGuardError):
    """Raised when duration_minutes is invalid or missing."""

    def __init__(self, session_id: str, duration: int | None):
        super().__init__(f"Session {session_id} has invalid duration_minutes: {duration}")


def validate_executable_session(session: ExecutableSession) -> None:
    """Validate that an ExecutableSession passes all guards.

    Guards:
    - Must have plan_id
    - Must have session_template_id
    - Must have valid source (ai_plan for Phase 5 output)
    - Must have valid duration_minutes (> 0)

    Args:
        session: ExecutableSession to validate

    Raises:
        MissingPlanIdError: If plan_id is empty
        MissingSessionTemplateIdError: If session_template_id is empty
        InvalidSessionSourceError: If source is not "ai_plan"
        InvalidDurationError: If duration_minutes is invalid
    """
    if not session.plan_id:
        raise MissingPlanIdError()

    if not session.session_template_id:
        raise MissingSessionTemplateIdError(session.session_id)

    if session.source != "ai_plan":
        raise InvalidSessionSourceError(session.session_id, session.source)

    if not session.duration_minutes or session.duration_minutes <= 0:
        raise InvalidDurationError(session.session_id, session.duration_minutes)


def validate_executable_sessions(sessions: list[ExecutableSession]) -> None:
    """Validate that all ExecutableSessions pass guards.

    Args:
        sessions: List of ExecutableSession to validate

    Raises:
        ExecutionGuardError: If any session fails validation
    """
    for session in sessions:
        validate_executable_session(session)
