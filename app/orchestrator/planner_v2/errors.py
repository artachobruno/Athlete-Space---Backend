"""DEPRECATED — B8.4 — Error types for orchestrator.

⚠️  THIS MODULE IS DEPRECATED ⚠️

This module is part of the legacy planner implementation and will be removed.
All planning should use the canonical planner: app.planner.plan_race_simple

This module defines error types used by the orchestrator for
hard stop semantics and retry logic.
"""

from app.planner.errors import PlannerError


class OrchestratorError(PlannerError):
    """Base exception for orchestrator errors."""

    pass


class ValidationError(OrchestratorError):
    """Raised when validation fails (hard stop, no retry)."""

    pass


class StepExecutionError(OrchestratorError):
    """Raised when a step execution fails.

    Attributes:
        step: Step name that failed
        original_error: Original exception that caused the failure
    """

    def __init__(self, step: str, original_error: Exception) -> None:
        self.step = step
        self.original_error = original_error
        super().__init__(f"Step '{step}' failed: {original_error}")


class LLMSchemaViolationError(OrchestratorError):
    """Raised when LLM schema validation fails (B6 only, allows retry)."""

    pass


class PersistenceError(OrchestratorError):
    """Raised when persistence fails (partial success allowed)."""

    pass
