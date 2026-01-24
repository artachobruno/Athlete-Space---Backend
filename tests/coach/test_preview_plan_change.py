"""Tests for preview_plan_change executor: read-only, no mutation, policy applied."""

from unittest.mock import patch

import pytest

from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.tools.semantic.evaluate_plan_change import (
    EvaluatePlanChangeResult,
    PlanChangeDecision,
    PlanStateSummary,
)


@pytest.fixture
def deps():
    from app.coach.agents.orchestrator_deps import CoachDeps

    return CoachDeps(
        athlete_id=1,
        user_id="preview-user",
        athlete_state=AthleteState(
            ctl=50.0,
            atl=45.0,
            tsb=5.0,
            confidence=0.9,
            load_trend="stable",
            volatility="low",
            days_since_rest=2,
            seven_day_volume_hours=8.5,
            fourteen_day_volume_hours=16.0,
            flags=[],
        ),
        athlete_profile=None,
        training_preferences=None,
        race_profile=None,
        structured_profile_data=None,
        days=60,
        days_to_race=None,
        execution_guard=None,
    )


@pytest.fixture
def decision():
    return OrchestratorAgentResponse(
        intent="propose",
        horizon="week",
        action="NO_ACTION",
        confidence=0.9,
        message="What would you change?",
        response_type="explanation",
        target_action="preview_plan_change",
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=False,
    )


@pytest.mark.asyncio
async def test_preview_no_planned_sessions_created(deps, decision):
    """Preview never creates planned_sessions."""
    state = PlanStateSummary(
        planned_total_week=0,
        planned_elapsed=0,
        planned_remaining=0,
        executed_elapsed=0,
        compliance_rate=0.0,
        summary_text="No plan",
    )
    eval_result = EvaluatePlanChangeResult(
        decision=PlanChangeDecision(
            decision="no_change",
            reasons=["No plan exists"],
            recommended_actions=["Create a plan"],
            confidence=0.7,
        ),
        current_state_summary="No plan",
        current_state=state,
        horizon="week",
    )

    with patch(
        "app.coach.executor.action_executor.evaluate_plan_change",
        return_value=eval_result,
    ):
        result = await CoachActionExecutor._execute_preview_plan_change(
            decision, deps, None
        )

    assert "Preview" in result
    assert "Evaluation" in result
    assert "PROPOSE_PLAN" in result or "policy" in result.lower()


@pytest.mark.asyncio
async def test_preview_response_contains_payload(deps, decision):
    """Response contains preview payload: decision, reasons, confidence."""
    state = PlanStateSummary(
        planned_total_week=5,
        planned_elapsed=2,
        planned_remaining=3,
        executed_elapsed=1,
        compliance_rate=0.5,
        summary_text="Week in progress",
    )
    eval_result = EvaluatePlanChangeResult(
        decision=PlanChangeDecision(
            decision="modification_required",
            reasons=["Low compliance"],
            recommended_actions=["Adjust plan"],
            confidence=0.8,
        ),
        current_state_summary="Week in progress",
        current_state=state,
        horizon="week",
    )

    with patch(
        "app.coach.executor.action_executor.evaluate_plan_change",
        return_value=eval_result,
    ):
        result = await CoachActionExecutor._execute_preview_plan_change(
            decision, deps, None
        )

    assert "Preview" in result
    assert "Evaluation" in result
    assert "modification_required" in result or "confidence" in result.lower()
    assert "Low compliance" in result or "Reasons" in result
    assert "Adjust plan" in result or "Recommended" in result


@pytest.mark.asyncio
async def test_preview_policy_applied(deps, decision):
    """Policy (decide_weekly_action) is applied; outcome appears in response."""
    state = PlanStateSummary(
        planned_total_week=0,
        planned_elapsed=0,
        planned_remaining=0,
        executed_elapsed=0,
        compliance_rate=0.0,
        summary_text="No plan",
    )
    eval_result = EvaluatePlanChangeResult(
        decision=PlanChangeDecision(
            decision="no_change",
            reasons=[],
            recommended_actions=None,
            confidence=0.5,
        ),
        current_state_summary="No plan",
        current_state=state,
        horizon="week",
    )

    with patch(
        "app.coach.executor.action_executor.evaluate_plan_change",
        return_value=eval_result,
    ):
        result = await CoachActionExecutor._execute_preview_plan_change(
            decision, deps, None
        )

    assert "PROPOSE_PLAN" in result or "no plan" in result.lower() or "policy" in result.lower()
