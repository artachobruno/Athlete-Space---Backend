"""Weekly Policy v0.

Purpose:
- Decide whether the coach should propose a plan or adjustment
- Based ONLY on evaluated state (Planning Model B+)
- No planning, no execution, no mutation

Design principles:
- Conservative
- Explicit
- Deterministic
- Easy to replace

This policy answers ONE question:
"Should the coach act this week?"
"""

from dataclasses import dataclass
from enum import Enum, StrEnum

from app.tools.semantic.evaluate_plan_change import PlanStateSummary


class WeeklyDecision(StrEnum):
    PROPOSE_PLAN = "PROPOSE_PLAN"
    PROPOSE_ADJUSTMENT = "PROPOSE_ADJUSTMENT"
    NO_CHANGE = "NO_CHANGE"


@dataclass(frozen=True)
class WeeklyPolicyResult:
    decision: WeeklyDecision
    reason: str


def decide_weekly_action(state: PlanStateSummary) -> WeeklyPolicyResult:
    """Weekly Policy v0 rules (in priority order).

    1. No plan exists → propose a plan
    2. Plan exists, elapsed compliance is poor → propose adjustment
    3. Otherwise → no change

    Notes:
    - Uses ONLY elapsed plan for compliance
    - Never inspects future sessions directly
    - Never considers fatigue, race proximity, or load trends (v1+)
    """
    # Rule 1 — No plan at all
    if state.planned_total_week == 0:
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_PLAN,
            reason="No training plan exists for the current week",
        )

    # Rule 2 — Plan exists, but athlete is off-track so far
    if state.planned_elapsed > 0 and state.compliance_rate < 0.5:
        return WeeklyPolicyResult(
            decision=WeeklyDecision.PROPOSE_ADJUSTMENT,
            reason=(
                f"Low compliance so far this week "
                f"({state.executed_elapsed}/{state.planned_elapsed} sessions completed)"
            ),
        )

    # Rule 3 — Default: do nothing
    return WeeklyPolicyResult(
        decision=WeeklyDecision.NO_CHANGE,
        reason="Training is on track; no changes needed",
    )
