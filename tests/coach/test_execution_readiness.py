"""Execution readiness tests.

Ensures EXECUTE with incomplete extraction (e.g. missing change_type)
raises NoActionError instead of crashing. Clarification happens at orchestration layer.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.coach.executor.action_executor import CoachActionExecutor
from app.coach.executor.errors import NoActionError
from app.coach.extraction.modify_week_extractor import ExtractedWeekModification
from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse


@pytest.fixture
def deps():
    """Create CoachDeps for execution readiness tests."""
    from app.coach.agents.orchestrator_deps import CoachDeps

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
    return CoachDeps(
        athlete_id=1,
        user_id="test-user",
        athlete_state=state,
        athlete_profile=None,
        training_preferences=None,
        race_profile=None,
        structured_profile_data=None,
        days=60,
        days_to_race=None,
        execution_guard=None,
    )


@pytest.mark.asyncio
async def test_execute_modify_week_missing_change_type_does_not_crash(deps):
    """EXECUTE with missing change_type raises NoActionError; no crash."""
    decision = OrchestratorAgentResponse(
        intent="modify",
        horizon="week",
        action="EXECUTE",
        confidence=0.9,
        message="Change my week somehow",
        response_type="plan",
        target_action="modify_week",
        required_attributes=[],
        optional_attributes=[],
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=True,
    )

    extracted_no_change_type = ExtractedWeekModification(
        horizon="week",
        change_type=None,
        reason=None,
    )

    with patch(
        "app.coach.executor.action_executor.extract_week_modification_llm",
        new_callable=AsyncMock,
        return_value=extracted_no_change_type,
    ):
        with pytest.raises(NoActionError) as exc_info:
            await CoachActionExecutor.execute(decision, deps)

    assert exc_info.value.code == "insufficient_modification_spec"
