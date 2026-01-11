"""MaterializedPlan - Complete Plan with All Weeks.

The final output of the planning system.
All weeks are materialized and validated.
"""

from dataclasses import dataclass

from app.planning.output.models import WeekPlan


@dataclass(frozen=True)
class MaterializedPlan:
    """Complete plan with all weeks materialized.

    This is the final output of the planning system.
    All weeks are fully materialized and validated.

    Attributes:
        plan_id: Unique plan identifier
        weeks: List of materialized weeks (in order)
    """

    plan_id: str
    weeks: list[WeekPlan]
