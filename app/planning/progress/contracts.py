"""Planning Progress Event Contracts - Phase 6B.

Canonical progress event structure for planning phases.
All events are structured, side-effect free, and emitted at phase boundaries.
"""

from dataclasses import dataclass
from typing import Literal

ProgressPhase = Literal[
    "plan_spec",
    "week_skeleton",
    "time_allocation",
    "week_validation",
    "week_assembly",
    "template_selection",
    "materialization",
    "materialization_validation",
    "execution",
]

# Type for summary values - allows primitives, lists, and nested dicts
SummaryPrimitive = str | int | float | bool | None
SummaryList = list[int] | list[str] | list[float]
SummaryNestedDict = dict[str, SummaryPrimitive]
SummaryValue = SummaryPrimitive | SummaryList | SummaryNestedDict


@dataclass(frozen=True)
class PlanningProgressEvent:
    """Planning progress event emitted at phase boundaries.

    Phase 6B: Structured, side-effect free events for frontend progress tracking.

    Attributes:
        phase: Planning phase identifier
        status: Event status (started or completed)
        percent_complete: Progress percentage (0-100, monotonically increasing)
        message: Human-readable message for this phase
        summary: Optional structured data (dict only, no free text)
        Values can be primitives, lists, or nested dicts (recursive structure)
    """

    phase: ProgressPhase
    status: Literal["started", "completed"]
    percent_complete: int
    message: str
    summary: dict[str, SummaryValue] | None = None
