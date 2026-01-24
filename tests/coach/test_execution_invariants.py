"""Execution invariants: adjust atomic, no questions; plan persistence failure fatal."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.executor.errors import InvalidModificationSpecError, PersistenceError
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse


@pytest.fixture
def deps():
    from app.coach.agents.orchestrator_deps import CoachDeps

    return CoachDeps(
        athlete_id=1,
        user_id="invariant-user",
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


@pytest.mark.asyncio
async def test_adjust_never_asks_questions(deps):
    """Adjust with no percentage raises InvalidModificationSpecError; executor never asks."""
    decision = OrchestratorAgentResponse(
        intent="adjust",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Reduce volume this week",
        response_type="plan",
        target_action="adjust_training_load",
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=False,
    )

    with pytest.raises(InvalidModificationSpecError) as exc_info:
        await CoachActionExecutor.execute(decision, deps)

    assert "missing_adjustment_amount" in exc_info.value.message


@pytest.mark.asyncio
async def test_adjust_executes_atomically(deps):
    """Reduce volume this week by 20% executes atomically; no slot flow."""
    decision = OrchestratorAgentResponse(
        intent="adjust",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Reduce volume this week by 20%",
        response_type="plan",
        target_action="adjust_training_load",
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=False,
    )

    mock_result = {"message": "I've adjusted your weekly plan. Total volume reduced by 20%."}

    with patch(
        "app.coach.executor.action_executor.call_tool",
        new_callable=AsyncMock,
        return_value=mock_result,
    ) as mock_call:
        out = await CoachActionExecutor.execute(decision, deps)
        mock_call.assert_called_once()
        call_args = mock_call.call_args[0]
        assert call_args[0] == "adjust_training_load"
        assert call_args[1].get("volume_delta_pct") == -0.20

    assert "adjusted" in out.lower() or "20%" in out


@pytest.mark.asyncio
async def test_plan_persistence_failure_is_fatal():
    """Calendar persistence failure raises PersistenceError; no mixed success."""
    from app.coach.tools.plan_week import plan_week
    from app.planner.calendar_persistence import PersistResult

    state = AthleteState(
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
    )

    async def _mock_pipeline(*, ctx, athlete_state, user_id, athlete_id, plan_id, base_volume_calculator):
        await asyncio.sleep(0)
        planned = [MagicMock(sessions=[MagicMock() for _ in range(7)], week_index=0)]
        persist = PersistResult(
            plan_id=plan_id,
            created=0,
            updated=0,
            skipped=0,
            warnings=[],
            success=False,
            session_ids=[],
        )
        return planned, persist

    mock_user = MagicMock()
    mock_user.timezone = "UTC"
    monday_utc = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)

    with (
        patch(
            "app.coach.tools.plan_week.execute_canonical_pipeline",
            side_effect=_mock_pipeline,
        ),
        patch("app.coach.tools.plan_week._check_weekly_plan_exists", return_value=False),
        patch("app.coach.tools.plan_week.build_training_summary") as mock_summary,
        patch("app.coach.tools.plan_week.now_user", return_value=monday_utc),
        patch("app.coach.tools.plan_week.to_utc", side_effect=lambda x: x),
        patch("app.coach.tools.plan_week.get_session") as mock_session,
    ):
        mock_summary.return_value = MagicMock(
            volume={"total_duration_minutes": 0},
            execution={"compliance_rate": 0.0, "completed_sessions": 0},
            load={"ctl": 50.0, "atl": 45.0, "tsb": 5.0},
            reliability_flags=MagicMock(high_variance=False),
        )
        mock_session.return_value.__enter__.return_value.execute.return_value.first.return_value = (
            mock_user,
        )

        with pytest.raises(PersistenceError) as exc_info:
            await plan_week(state=state, user_id="u", athlete_id=1, user_feedback=None)

        assert exc_info.value.message == "plan_commit_failed"


@pytest.mark.asyncio
async def test_plan_fails_if_calendar_persistence_fails(deps):
    """Execute('Create a training plan for this week') raises PersistenceError when persist fails."""
    from app.coach.mcp_client import MCPError
    from app.orchestrator.routing import RoutedTool

    decision = OrchestratorAgentResponse(
        intent="plan",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Create a training plan for this week",
        response_type="plan",
        target_action="plan_week",
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=True,
    )

    def _mock_plan_week_raises(*args, **kwargs):
        raise MCPError("CALENDAR_PERSISTENCE_FAILED", "calendar_persistence_failed")

    with (
        patch(
            "app.coach.executor.action_executor.route_with_safety_check",
            return_value=(RoutedTool(name="plan", mode="CREATE"), []),
        ),
        patch(
            "app.coach.executor.action_executor.call_tool",
            new_callable=AsyncMock,
            side_effect=_mock_plan_week_raises,
        ),
    ):
        with pytest.raises(PersistenceError) as exc_info:
            await CoachActionExecutor.execute(decision, deps)

        assert exc_info.value.message == "plan_commit_failed"


@pytest.mark.asyncio
async def test_plan_week_fails_if_calendar_persistence_fails():
    """plan_week raises PersistenceError when persist fails."""
    from app.coach.tools.plan_week import plan_week

    state = AthleteState(
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
    )

    async def _mock_pipeline_raises(*, ctx, athlete_state, user_id, athlete_id, plan_id, base_volume_calculator):
        await asyncio.sleep(0)
        raise RuntimeError("db down")

    mock_user = MagicMock()
    mock_user.timezone = "UTC"
    monday_utc = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)

    with (
        patch(
            "app.coach.tools.plan_week.execute_canonical_pipeline",
            side_effect=_mock_pipeline_raises,
        ),
        patch("app.coach.tools.plan_week._check_weekly_plan_exists", return_value=False),
        patch("app.coach.tools.plan_week.build_training_summary") as mock_summary,
        patch("app.coach.tools.plan_week.now_user", return_value=monday_utc),
        patch("app.coach.tools.plan_week.to_utc", side_effect=lambda x: x),
        patch("app.coach.tools.plan_week.get_session") as mock_session,
    ):
        mock_summary.return_value = MagicMock(
            volume={"total_duration_minutes": 0},
            execution={"compliance_rate": 0.0, "completed_sessions": 0},
            load={"ctl": 50.0, "atl": 45.0, "tsb": 5.0},
            reliability_flags=MagicMock(high_variance=False),
        )
        mock_session.return_value.__enter__.return_value.execute.return_value.first.return_value = (
            mock_user,
        )

        with pytest.raises(PersistenceError) as exc_info:
            await plan_week(state=state, user_id="u", athlete_id=1, user_feedback=None)

        assert exc_info.value.message == "plan_commit_failed"


@pytest.mark.asyncio
async def test_plan_week_fails_if_not_persisted(deps):
    """_execute_plan_week raises PersistenceError when tool returns success with 'not saved' or 'calendar unavailable'."""
    from app.orchestrator.routing import RoutedTool

    decision = OrchestratorAgentResponse(
        intent="plan",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Create a training plan for this week",
        response_type="plan",
        target_action="plan_week",
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=True,
    )

    def _mock_plan_week_return_not_saved(*args, **kwargs):
        return {"message": "Plan generated but not saved - calendar unavailable."}

    with (
        patch(
            "app.coach.executor.action_executor.route_with_safety_check",
            return_value=(RoutedTool(name="plan", mode="CREATE"), []),
        ),
        patch(
            "app.coach.executor.action_executor.call_tool",
            new_callable=AsyncMock,
            side_effect=_mock_plan_week_return_not_saved,
        ),
    ):
        with pytest.raises(PersistenceError) as exc_info:
            await CoachActionExecutor.execute(decision, deps)

        assert exc_info.value.message == "plan_commit_failed"
