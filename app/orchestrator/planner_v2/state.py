"""DEPRECATED — B8.1 — Planner execution state (single source of truth).

⚠️  THIS MODULE IS DEPRECATED ⚠️

This module is part of the legacy planner implementation and will be removed.
All planning should use the canonical planner: app.planner.plan_race_simple

This module defines the PlannerV2State dataclass that tracks all artifacts
produced during plan execution. State is append-only - steps may read previous
fields only and never mutate previous artifacts.
"""

from dataclasses import dataclass, replace

from app.coach.schemas.athlete_state import AthleteState
from app.planner.calendar_persistence import PersistResult
from app.planner.models import (
    DistributedDay,
    MacroWeek,
    PlanContext,
    PlannedWeek,
    PlanRuntimeContext,
    WeekStructure,
)


@dataclass(frozen=True)
class PlannerV2State:
    """Immutable planner execution state.

    This is the single source of truth for planner execution.
    All steps read from and append to this state.

    Rules:
    - State is append-only
    - Steps may read previous fields only
    - Never mutate previous artifacts
    - Use replace() to create new state instances

    Attributes:
        plan_id: Unique plan identifier
        ctx: Plan context (immutable)
        athlete_state: Athlete state snapshot
        macro_plan: Macro weeks from B2 (None until B2 completes)
        philosophy_id: Selected philosophy ID from B2.5 (None until B2.5 completes)
        structure: PlanRuntimeContext with philosophy from B2.5 (None until B2.5 completes)
        distributed_days_by_week: List of DistributedDay lists per week from B4 (None until B4 completes)
        templated_weeks: Weeks with templates selected from B5 (None until B5 completes)
        text_weeks: Weeks with session text from B6 (None until B6 completes)
        persist_result: Persistence result from B7 (None until B7 completes)
        current_step: Current execution step name
    """

    plan_id: str
    ctx: PlanContext
    athlete_state: AthleteState

    macro_plan: list[MacroWeek] | None = None
    philosophy_id: str | None = None
    structure: PlanRuntimeContext | None = None
    week_structures: list[WeekStructure] | None = None
    distributed_days_by_week: list[list[DistributedDay]] | None = None
    templated_weeks: list[PlannedWeek] | None = None
    text_weeks: list[PlannedWeek] | None = None

    persist_result: PersistResult | None = None

    current_step: str = "init"

    def replace(self, **changes: object) -> "PlannerV2State":
        """Create a new state instance with updated fields.

        This is a convenience method that wraps dataclasses.replace()
        to ensure type safety.

        Args:
            **changes: Fields to update

        Returns:
            New PlannerV2State instance with updated fields
        """
        return replace(self, **changes)
