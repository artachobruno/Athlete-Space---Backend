"""Execution Contracts - Phase 6A.

Formalize what may be written to the calendar.
Pure execution data only - no structure fields, no intervals, no LLM text.
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

ExecutionSource = Literal["ai_plan", "manual", "import"]


@dataclass(frozen=True)
class ExecutableSession:
    """Executable session that may be written to the calendar.

    This is pure execution data - no planning structure, no intervals, no LLM text.
    All fields are required for execution.

    Attributes:
        session_id: Stable UUID for this session
        plan_id: Plan identifier this session belongs to
        week_index: Zero-based week index in the plan
        date: Actual calendar date for this session
        duration_minutes: Duration in minutes
        distance_miles: Distance in miles
        session_type: Type of session (e.g., "easy", "long", "tempo")
        session_template_id: Template ID this session is based on
        source: Source of this session (ai_plan, manual, or import)
    """

    session_id: str
    plan_id: str
    week_index: int
    date: date

    duration_minutes: int
    distance_miles: float

    session_type: str
    session_template_id: str

    source: ExecutionSource
