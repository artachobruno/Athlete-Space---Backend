"""Domain-specific errors for planning stages.

This module defines error classes for each planning stage,
enabling precise error handling and debugging.
"""


class PlannerError(Exception):
    """Base exception for all planning errors."""

    pass


class InvalidPlanContextError(PlannerError):
    """Raised when plan context is invalid (e.g., race plan without distance)."""

    pass


class InvalidMacroPlanError(PlannerError):
    """Raised when macro plan structure is invalid (e.g., wrong week count)."""

    pass


class UnknownWeekFocusError(PlannerError):
    """Raised when week focus is not recognized or not RAG-compatible."""

    pass


class InvalidSkeletonError(PlannerError):
    """Raised when week skeleton violates structural constraints."""

    pass


class VolumeAllocationError(PlannerError):
    """Raised when volume allocation fails (e.g., cannot fit sessions)."""

    pass


class TemplateSelectionError(PlannerError):
    """Raised when template selection fails (e.g., no matching templates)."""

    pass


class PlannerInvariantError(PlannerError):
    """Raised when a planning invariant is violated between stages."""

    pass


class PlannerAbort(PlannerError):
    """Raised when plan generation must be aborted (e.g., zero sessions created)."""

    pass
