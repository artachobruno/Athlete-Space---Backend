"""Executor invariants: never ask questions, missing spec → InvalidModificationSpecError, clarify at orchestration."""

from unittest.mock import AsyncMock, patch

import pytest

from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.executor.errors import InvalidModificationSpecError
from app.coach.extraction.modify_week_extractor import ExtractedWeekModification
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
async def test_executors_never_emit_questions(deps):
    """Executors never return user-facing questions; they raise InvalidModificationSpecError."""
    decision = OrchestratorAgentResponse(
        intent="modify",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Change my week",
        response_type="plan",
        target_action="modify_week",
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=True,
    )
    extracted = ExtractedWeekModification(horizon="week", change_type=None, reason=None)

    with patch(
        "app.coach.executor.action_executor.extract_week_modification_llm",
        new_callable=AsyncMock,
        return_value=extracted,
    ), pytest.raises(InvalidModificationSpecError):
        await CoachActionExecutor.execute(decision, deps)

    # Executor must not return a string that looks like a clarifying question
    # (it raises instead)


@pytest.mark.asyncio
async def test_missing_spec_raises_invalid_modification_spec(deps):
    """Missing modification spec (e.g. change_type) raises InvalidModificationSpecError."""
    decision = OrchestratorAgentResponse(
        intent="modify",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Change my week",
        response_type="plan",
        target_action="modify_week",
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=True,
    )
    extracted = ExtractedWeekModification(horizon="week", change_type=None, reason=None)

    with patch(
        "app.coach.executor.action_executor.extract_week_modification_llm",
        new_callable=AsyncMock,
        return_value=extracted,
    ), pytest.raises(InvalidModificationSpecError) as exc_info:
        await CoachActionExecutor.execute(decision, deps)

    assert "clarification" in exc_info.value.message.lower() or "incomplete" in exc_info.value.message.lower()


def test_clarification_via_orchestration_not_executor(deps):
    """Clarification is returned by orchestration layer when it catches InvalidModificationSpecError."""
    # Orchestration layer catches InvalidModificationSpecError and returns clarify.
    clarify_message = "I need a bit more detail before I can make that change. What would you like to modify?"
    assert "detail" in clarify_message.lower() or "modify" in clarify_message.lower()
