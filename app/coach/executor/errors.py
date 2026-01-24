"""Executor-specific errors.

Raised when execution cannot proceed due to insufficient spec.
Must be handled by orchestration layer (clarify, preserve horizon).
"""


class NoActionError(Exception):
    """Raised when execution cannot proceed due to insufficient spec.

    Must be handled by orchestrator: return intent=clarify, preserve horizon,
    ask clarifying question at orchestration layer only.
    """

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or f"Execution blocked: {code}"
        super().__init__(self.message)


class InvalidModificationSpecError(Exception):
    """Raised when modification spec is incomplete; clarification must occur before execution."""

    def __init__(self, message: str = "Incomplete modification spec — clarification must occur before execution"):
        self.message = message
        super().__init__(self.message)


class ExecutionError(Exception):
    """Raised when execution fails (e.g. calendar persistence)."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or f"Execution failed: {code}"
        super().__init__(self.message)


class PersistenceError(RuntimeError):
    """Raised when a generated plan cannot be persisted."""

    def __init__(self, message: str = "plan_commit_failed"):
        self.message = message
        super().__init__(self.message)
