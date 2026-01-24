from app.coach.policy.weekly_policy_v0 import (
    WeeklyDecision,
    decide_weekly_action,
)
from app.tools.semantic.evaluate_plan_change import PlanStateSummary


def make_state(
    *,
    planned_total_week: int,
    planned_elapsed: int,
    planned_remaining: int,
    executed_elapsed: int,
    compliance_rate: float,
):
    return PlanStateSummary(
        planned_total_week=planned_total_week,
        planned_elapsed=planned_elapsed,
        planned_remaining=planned_remaining,
        executed_elapsed=executed_elapsed,
        compliance_rate=compliance_rate,
        summary_text="",
    )


def test_propose_plan_when_no_plan_exists():
    state = make_state(
        planned_total_week=0,
        planned_elapsed=0,
        planned_remaining=0,
        executed_elapsed=0,
        compliance_rate=1.0,
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_PLAN


def test_propose_adjustment_when_compliance_low():
    state = make_state(
        planned_total_week=6,
        planned_elapsed=4,
        planned_remaining=2,
        executed_elapsed=1,
        compliance_rate=0.25,
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.PROPOSE_ADJUSTMENT


def test_no_change_when_on_track():
    state = make_state(
        planned_total_week=6,
        planned_elapsed=3,
        planned_remaining=3,
        executed_elapsed=3,
        compliance_rate=1.0,
    )

    result = decide_weekly_action(state)

    assert result.decision == WeeklyDecision.NO_CHANGE
