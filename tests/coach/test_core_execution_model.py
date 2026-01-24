"""Minimal tests for core execution model.

- Create path: "Create a training plan for this week" → plan.CREATE → _execute_plan_week
- Modify path: "Reduce mileage this week" → modify.MODIFY → _execute_modify_week (after preview/confirm)
- Incomplete modify: "Change my plan" → clarify → executor never runs
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.orchestrator.routing import route_with_safety_check


@pytest.fixture
def deps():
    from app.coach.agents.orchestrator_deps import CoachDeps

    return CoachDeps(
        athlete_id=1,
        user_id="test-user",
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


def test_create_path_plan_create_routes_to_plan():
    """Create a training plan for this week → plan + CREATE."""
    with patch("app.orchestrator.routing.has_existing_plan", return_value=False):
        rt, _ = route_with_safety_check(
            intent="plan",
            horizon="week",
            has_proposal=False,
            athlete_id=1,
        )
    assert rt is not None
    assert rt.name == "plan"
    assert rt.mode == "CREATE"


def test_modify_path_modify_mode():
    """Reduce mileage this week → modify + MODIFY."""
    with patch("app.orchestrator.routing.has_existing_plan", return_value=True):
        rt, _ = route_with_safety_check(
            intent="plan",
            horizon="week",
            has_proposal=False,
            athlete_id=1,
        )
    assert rt is not None
    assert rt.name == "modify"
    assert rt.mode == "MODIFY"

    rt2, _ = route_with_safety_check(
        intent="modify",
        horizon="week",
        has_proposal=False,
        athlete_id=1,
    )
    assert rt2 is not None
    assert rt2.name == "modify"
    assert rt2.mode == "MODIFY"


def test_adjust_week_routes_to_adjust_training_load():
    """Reduce volume this week → adjust + week → adjust_training_load; executor accepts it."""
    rt, _ = route_with_safety_check(
        intent="adjust",
        horizon="week",
        has_proposal=False,
        athlete_id=1,
    )
    assert rt is not None
    assert rt.name == "adjust_training_load"

    is_valid, _ = CoachActionExecutor._validate_intent_horizon_combination("adjust", "week")
    assert is_valid is True


def test_adjust_season_routes_to_adjust_training_load():
    """Adjust season volume → adjust + season → adjust_training_load; executor accepts it."""
    rt, _ = route_with_safety_check(
        intent="adjust",
        horizon="season",
        has_proposal=False,
        athlete_id=1,
    )
    assert rt is not None
    assert rt.name == "adjust_training_load"

    is_valid, _ = CoachActionExecutor._validate_intent_horizon_combination("adjust", "season")
    assert is_valid is True


@pytest.mark.asyncio
async def test_incomplete_modify_clarify_executor_never_runs(deps):
    """Change my plan → clarify; executor raises InvalidModificationSpecError, never returns question."""
    from app.coach.executor.errors import InvalidModificationSpecError

    decision = OrchestratorAgentResponse(
        intent="modify",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Change my plan",
        response_type="plan",
        target_action="modify_week",
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=True,
    )

    from app.coach.extraction.modify_week_extractor import ExtractedWeekModification

    with patch(
        "app.coach.executor.action_executor.extract_week_modification_llm",
        new_callable=AsyncMock,
        return_value=ExtractedWeekModification(horizon="week", change_type=None, reason=None),
    ), pytest.raises(InvalidModificationSpecError):
        await CoachActionExecutor.execute(decision, deps)
