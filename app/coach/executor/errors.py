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
