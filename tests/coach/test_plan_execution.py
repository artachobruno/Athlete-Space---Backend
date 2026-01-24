"""Execution phase tests (Phase C).

Cursor-style determinism: ActionPlan before execution, ordered steps,
planned -> in_progress -> completed, failure halts, no routing bypass.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.schemas.action_plan import ActionPlan, ActionStep
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse


@pytest.fixture
def mock_deps():
    return CoachDeps(athlete_id=1, user_id="test-user", athlete_state=None)


@pytest.fixture
def mock_decision():
    return OrchestratorAgentResponse(
        intent="plan",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Plan created.",
        response_type="weekly_plan",
        target_action="plan_week",
        filled_slots={},
        missing_slots=[],
        should_execute=True,
    )


@pytest.mark.asyncio
async def test_action_plan_generated_before_execution(mock_deps, mock_decision):
    """ActionPlan is generated before execution."""
    from app.coach.flows.plan_execution import execute_plan_with_action_plan

    plan_generated = []

    def capture_generate(intent, horizon):
        import app.coach.flows.plan_execution as mod

        generate_func = mod._generate_action_plan
        result = generate_func(intent, horizon)
        plan_generated.append(result)
        return result

    with (
        patch("app.coach.flows.plan_execution.require_authorization"),
        patch("app.coach.flows.plan_execution.emit_progress_event_safe", new_callable=AsyncMock),
        patch("app.coach.flows.plan_execution.route_with_safety_check", return_value=("plan", [])),
        patch("app.coach.flows.plan_execution.require_recent_evaluation", new_callable=AsyncMock),
        patch("app.coach.flows.plan_execution.execute_semantic_tool", new_callable=AsyncMock, return_value="ok"),
        patch("app.coach.flows.plan_execution._generate_action_plan", side_effect=capture_generate),
    ):
        await execute_plan_with_action_plan(
            mock_decision,
            mock_deps,
            "c_test",
            "week",
        )

    assert len(plan_generated) == 1
    ap = plan_generated[0]
    assert isinstance(ap, ActionPlan)
    assert len(ap.steps) >= 1
    step_ids = [s.id for s in ap.steps]
    assert "load_training_state" in step_ids
    assert "save_plan" in step_ids


@pytest.mark.asyncio
async def test_steps_execute_in_order(mock_deps, mock_decision):
    """Steps execute in order."""
    from app.coach.flows.plan_execution import execute_plan_with_action_plan

    order = []

    def fake_execute_step(step, decision, deps, conversation_id, horizon):
        order.append(step.id)
        return "ok"

    with (
        patch("app.coach.flows.plan_execution.require_authorization"),
        patch("app.coach.flows.plan_execution.emit_progress_event_safe", new_callable=AsyncMock),
        patch("app.coach.flows.plan_execution._execute_step", side_effect=fake_execute_step),
    ):
        out = await execute_plan_with_action_plan(
            mock_decision,
            mock_deps,
            "c_test",
            "week",
        )

    assert order == list(out["completed_steps"])
    assert len(order) >= 1


@pytest.mark.asyncio
async def test_each_step_emits_planned_in_progress_completed(mock_deps, mock_decision):
    """Each step emits planned, in_progress, completed."""
    from app.coach.flows.plan_execution import execute_plan_with_action_plan

    emitted = []

    def capture_emit(conversation_id, step_id, label, status, message=None):
        emitted.append((step_id, status))

    with (
        patch("app.coach.flows.plan_execution.require_authorization"),
        patch("app.coach.flows.plan_execution.emit_progress_event_safe", side_effect=capture_emit),
        patch("app.coach.flows.plan_execution.route_with_safety_check", return_value=("plan", [])),
        patch("app.coach.flows.plan_execution.require_recent_evaluation", new_callable=AsyncMock),
        patch("app.coach.flows.plan_execution.execute_semantic_tool", new_callable=AsyncMock, return_value="ok"),
    ):
        await execute_plan_with_action_plan(
            mock_decision,
            mock_deps,
            "c_test",
            "week",
        )

    by_step = {}
    for step_id, status in emitted:
        by_step.setdefault(step_id, []).append(status)

    for statuses in by_step.values():
        assert "planned" in statuses
        assert "in_progress" in statuses
        assert "completed" in statuses


@pytest.mark.asyncio
async def test_failure_stops_execution(mock_deps, mock_decision):
    """Failure halts execution."""
    from app.coach.flows.plan_execution import execute_plan_with_action_plan

    call_count = 0

    def fail_on_second(step, decision, deps, conversation_id, horizon):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise RuntimeError("step failed")
        return "ok"

    with (
        patch("app.coach.flows.plan_execution.require_authorization"),
        patch("app.coach.flows.plan_execution.emit_progress_event_safe", new_callable=AsyncMock),
        patch("app.coach.flows.plan_execution._execute_step", side_effect=fail_on_second), pytest.raises(RuntimeError, match="failed")
    ):
        await execute_plan_with_action_plan(
            mock_decision,
            mock_deps,
            "c_test",
            "week",
        )

    assert call_count == 2


@pytest.mark.asyncio
async def test_execution_uses_route_with_safety_check(mock_deps, mock_decision):
    """No execution bypasses route_with_safety_check."""
    from app.coach.flows.plan_execution import execute_plan_with_action_plan

    route_calls = []

    def capture_route(intent, horizon, has_proposal=False, needs_approval=False, query_type=None, run_incoherence_check=True):
        route_calls.append((intent, horizon))
        return ("plan", [])

    with (
        patch("app.coach.flows.plan_execution.require_authorization"),
        patch("app.coach.flows.plan_execution.emit_progress_event_safe", new_callable=AsyncMock),
        patch("app.coach.flows.plan_execution.route_with_safety_check", side_effect=capture_route),
        patch("app.coach.flows.plan_execution.require_recent_evaluation", new_callable=AsyncMock),
        patch("app.coach.flows.plan_execution.execute_semantic_tool", new_callable=AsyncMock, return_value="ok"),
    ):
        await execute_plan_with_action_plan(
            mock_decision,
            mock_deps,
            "c_test",
            "week",
        )

    assert len(route_calls) >= 1
    intents = [r[0] for r in route_calls]
    assert "plan" in intents or mock_decision.intent in intents
