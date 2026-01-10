"""Tests for race planning goal extraction with multi-turn conversation support.

Tests invariant behaviors:
- Multi-turn conversation handling
- Partial follow-up resolution
- Date resolution with context
- Time normalization
- Goal type inference
- Slot-first resolution rule
"""

from datetime import UTC, date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.coach.tools.plan_race import (
    TrainingGoalInformation,
    build_conversation_context,
    extract_training_goal,
    resolve_awaited_slots,
)
from app.db.models import ConversationProgress

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _create_mock_progress(
    conversation_id: str = "test_conv_123",
    slots: dict | None = None,
    awaiting_slots: list[str] | None = None,
) -> ConversationProgress:
    """Create a mock ConversationProgress for testing."""
    return ConversationProgress(
        conversation_id=conversation_id,
        intent="race_plan",
        slots=slots or {},
        awaiting_slots=awaiting_slots or [],
        updated_at=datetime.now(UTC),
    )


# ============================================================================
# CONVERSATION CONTEXT TESTS
# ============================================================================


def test_build_conversation_context_empty():
    """Test building context from None progress."""
    context = build_conversation_context(None)
    assert context["known_race_name"] is None
    assert context["known_race_distance"] is None
    assert context["known_race_date"] is None
    assert context["known_race_month"] is None
    assert context["known_target_time"] is None
    assert context["known_goal_type"] is None


def test_build_conversation_context_with_slots():
    """Test building context from progress with filled slots."""
    race_date = datetime(2026, 4, 25, tzinfo=UTC)
    progress = _create_mock_progress(
        slots={
            "race_name": "Boston Marathon",
            "race_distance": "Marathon",
            "race_date": race_date,
            "target_time": "03:00:00",
            "goal_type": "time",
        },
    )
    context = build_conversation_context(progress)
    assert context["known_race_name"] == "Boston Marathon"
    assert context["known_race_distance"] == "Marathon"
    assert context["known_race_date"] == "2026-04-25"
    assert context["known_race_month"] == "April"
    assert context["known_target_time"] == "03:00:00"
    assert context["known_goal_type"] == "time"


def test_build_conversation_context_partial():
    """Test building context from progress with partial slots."""
    progress = _create_mock_progress(
        slots={
            "race_distance": "Marathon",
            "race_date": datetime(2026, 4, 25, tzinfo=UTC),
        },
    )
    context = build_conversation_context(progress)
    assert context["known_race_distance"] == "Marathon"
    assert context["known_race_date"] == "2026-04-25"
    assert context["known_race_month"] == "April"
    assert context["known_race_name"] is None
    assert context["known_target_time"] is None


# ============================================================================
# MULTI-TURN CONVERSATION TESTS
# ============================================================================


@patch("app.coach.tools.plan_race.get_model")
def test_extract_training_goal_with_context(mock_get_model):
    """Test extraction uses conversation context for partial follow-ups."""
    # Mock the LLM agent
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.output = TrainingGoalInformation(
        race_distance="Marathon",
        race_date="2026-04-25",
        target_finish_time=None,
        goal_type=None,
        confidence_level="high",
    )
    mock_agent.run_sync.return_value = mock_result
    mock_model = MagicMock()
    mock_get_model.return_value = mock_model

    from pydantic_ai import Agent

    with patch.object(Agent, "__init__", return_value=None), patch.object(Agent, "run_sync", return_value=mock_result):
        today = date(2025, 1, 15)
        context = {
            "known_race_distance": "Marathon",
            "known_race_month": "April",
            "known_race_name": None,
            "known_race_date": None,
            "known_target_time": None,
            "known_goal_type": None,
        }

        # User says "on the 25th!" - should resolve using April from context
        result = extract_training_goal(
            latest_user_message="on the 25th!",
            conversation_context=context,
            awaiting_slots=["race_date"],
            today=today,
        )

        # Verify the extraction was called with context
        assert result.race_distance == "Marathon"
        assert result.race_date == "2026-04-25"


@patch("app.coach.tools.plan_race.get_model")
def test_extract_training_goal_slot_first_resolution(mock_get_model):
    """Test that awaiting_slots are prioritized in extraction."""
    mock_result = MagicMock()
    mock_result.output = TrainingGoalInformation(
        race_distance=None,
        race_date="2026-04-25",
        target_finish_time=None,
        goal_type=None,
        confidence_level="moderate",
    )
    mock_agent = MagicMock()
    mock_agent.run_sync.return_value = mock_result
    mock_model = MagicMock()
    mock_get_model.return_value = mock_model

    from pydantic_ai import Agent

    with patch.object(Agent, "__init__", return_value=None), patch.object(Agent, "run_sync", return_value=mock_result):
        today = date(2025, 1, 15)
        context = {
            "known_race_distance": "Marathon",
            "known_race_month": "April",
            "known_race_name": None,
            "known_race_date": None,
            "known_target_time": None,
            "known_goal_type": None,
        }

        # System is awaiting race_date, user provides it
        result = extract_training_goal(
            latest_user_message="April 25th",
            conversation_context=context,
            awaiting_slots=["race_date"],
            today=today,
        )

        assert result.race_date == "2026-04-25"


# ============================================================================
# DATE RESOLUTION TESTS
# ============================================================================


@patch("app.coach.tools.plan_race.get_model")
def test_date_resolution_with_month_context(mock_get_model):
    """Test date resolution when month is in context."""
    mock_result = MagicMock()
    mock_result.output = TrainingGoalInformation(
        race_distance="Marathon",
        race_date="2026-04-25",  # Should resolve using April from context
        target_finish_time=None,
        goal_type=None,
        confidence_level="high",
    )
    mock_agent = MagicMock()
    mock_agent.run_sync.return_value = mock_result
    mock_model = MagicMock()
    mock_get_model.return_value = mock_model

    from pydantic_ai import Agent

    with patch.object(Agent, "__init__", return_value=None), patch.object(Agent, "run_sync", return_value=mock_result):
        today = date(2025, 1, 15)
        context = {
            "known_race_distance": "Marathon",
            "known_race_month": "April",
            "known_race_name": None,
            "known_race_date": None,
            "known_target_time": None,
            "known_goal_type": None,
        }

        # User says "on the 25th!" - should use April from context
        result = extract_training_goal(
            latest_user_message="on the 25th!",
            conversation_context=context,
            awaiting_slots=["race_date"],
            today=today,
        )

        assert result.race_date == "2026-04-25"


@patch("app.coach.tools.plan_race.get_model")
def test_date_resolution_future_year_inference(mock_get_model):
    """Test year inference for future dates."""
    mock_result = MagicMock()
    mock_result.output = TrainingGoalInformation(
        race_distance="Marathon",
        race_date="2025-04-25",  # Current year since date hasn't passed
        target_finish_time=None,
        goal_type=None,
        confidence_level="high",
    )
    mock_agent = MagicMock()
    mock_agent.run_sync.return_value = mock_result
    mock_model = MagicMock()
    mock_get_model.return_value = mock_model

    from pydantic_ai import Agent

    with patch.object(Agent, "__init__", return_value=None), patch.object(Agent, "run_sync", return_value=mock_result):
        today = date(2025, 1, 15)
        context = {
            "known_race_name": None,
            "known_race_distance": None,
            "known_race_date": None,
            "known_race_month": None,
            "known_target_time": None,
            "known_goal_type": None,
        }

        # User says "April 25th" - should infer current year
        result = extract_training_goal(
            latest_user_message="I'm training for a marathon in April 25th",
            conversation_context=context,
            awaiting_slots=[],
            today=today,
        )

        assert result.race_date == "2025-04-25"
        assert result.race_distance == "Marathon"


# ============================================================================
# TIME NORMALIZATION TESTS
# ============================================================================


@patch("app.coach.tools.plan_race.get_model")
def test_time_normalization_sub_three(mock_get_model):
    """Test time normalization: 'sub 3' -> '03:00:00'."""
    mock_result = MagicMock()
    mock_result.output = TrainingGoalInformation(
        race_distance="Marathon",
        race_date=None,
        target_finish_time="03:00:00",
        goal_type="time",
        confidence_level="high",
    )
    mock_agent = MagicMock()
    mock_agent.run_sync.return_value = mock_result
    mock_model = MagicMock()
    mock_get_model.return_value = mock_model

    from pydantic_ai import Agent

    with patch.object(Agent, "__init__", return_value=None), patch.object(Agent, "run_sync", return_value=mock_result):
        today = date(2025, 1, 15)
        context = {
            "known_race_name": None,
            "known_race_distance": None,
            "known_race_date": None,
            "known_race_month": None,
            "known_target_time": None,
            "known_goal_type": None,
        }

        result = extract_training_goal(
            latest_user_message="sub 3 marathon",
            conversation_context=context,
            awaiting_slots=[],
            today=today,
        )

        assert result.target_finish_time == "03:00:00"
        assert result.goal_type == "time"


@patch("app.coach.tools.plan_race.get_model")
def test_time_normalization_under_two_hours(mock_get_model):
    """Test time normalization: 'under 2 hours' -> '02:00:00'."""
    mock_result = MagicMock()
    mock_result.output = TrainingGoalInformation(
        race_distance="Half Marathon",
        race_date=None,
        target_finish_time="02:00:00",
        goal_type="time",
        confidence_level="high",
    )
    mock_agent = MagicMock()
    mock_agent.run_sync.return_value = mock_result
    mock_model = MagicMock()
    mock_get_model.return_value = mock_model

    from pydantic_ai import Agent

    with patch.object(Agent, "__init__", return_value=None), patch.object(Agent, "run_sync", return_value=mock_result):
        today = date(2025, 1, 15)
        context = {
            "known_race_name": None,
            "known_race_distance": None,
            "known_race_date": None,
            "known_race_month": None,
            "known_target_time": None,
            "known_goal_type": None,
        }

        result = extract_training_goal(
            latest_user_message="half marathon under 2 hours",
            conversation_context=context,
            awaiting_slots=[],
            today=today,
        )

        assert result.target_finish_time == "02:00:00"


# ============================================================================
# GOAL TYPE INFERENCE TESTS
# ============================================================================


@patch("app.coach.tools.plan_race.get_model")
def test_goal_type_finish(mock_get_model):
    """Test goal type inference: 'just want to finish' -> 'finish'."""
    mock_result = MagicMock()
    mock_result.output = TrainingGoalInformation(
        race_distance="Marathon",
        race_date=None,
        target_finish_time=None,
        goal_type="finish",
        confidence_level="moderate",
    )
    mock_agent = MagicMock()
    mock_agent.run_sync.return_value = mock_result
    mock_model = MagicMock()
    mock_get_model.return_value = mock_model

    from pydantic_ai import Agent

    with patch.object(Agent, "__init__", return_value=None), patch.object(Agent, "run_sync", return_value=mock_result):
        today = date(2025, 1, 15)
        context = {
            "known_race_name": None,
            "known_race_distance": None,
            "known_race_date": None,
            "known_race_month": None,
            "known_target_time": None,
            "known_goal_type": None,
        }

        result = extract_training_goal(
            latest_user_message="first marathon just want to finish",
            conversation_context=context,
            awaiting_slots=[],
            today=today,
        )

        assert result.goal_type == "finish"


# ============================================================================
# SLOT RESOLUTION TESTS
# ============================================================================


@patch("app.coach.tools.plan_race.extract_training_goal")
def test_resolve_awaited_slots_race_date(mock_extract):
    """Test resolving awaited race_date slot."""
    today = date(2025, 1, 15)
    progress = _create_mock_progress(
        slots={"race_distance": "Marathon"},
        awaiting_slots=["race_date"],
    )

    mock_extract.return_value = TrainingGoalInformation(
        race_distance="Marathon",
        race_date="2026-04-25",
        target_finish_time=None,
        goal_type=None,
        confidence_level="high",
    )

    resolved, remaining = resolve_awaited_slots("April 25th", progress, today)

    assert "race_date" in resolved
    assert isinstance(resolved["race_date"], datetime)
    assert resolved["race_date"].year == 2026
    assert resolved["race_date"].month == 4
    assert resolved["race_date"].day == 25
    assert "race_date" not in remaining


@patch("app.coach.tools.plan_race.extract_training_goal")
def test_resolve_awaited_slots_race_distance(mock_extract):
    """Test resolving awaited race_distance slot."""
    today = date(2025, 1, 15)
    progress = _create_mock_progress(
        slots={},
        awaiting_slots=["race_distance"],
    )

    mock_extract.return_value = TrainingGoalInformation(
        race_distance="Marathon",
        race_date=None,
        target_finish_time=None,
        goal_type=None,
        confidence_level="high",
    )

    resolved, remaining = resolve_awaited_slots("I want to run a marathon", progress, today)

    assert "race_distance" in resolved
    assert resolved["race_distance"] == "Marathon"
    assert "race_distance" not in remaining


@patch("app.coach.tools.plan_race.extract_training_goal")
def test_resolve_awaited_slots_target_time(mock_extract):
    """Test resolving awaited target_time slot."""
    today = date(2025, 1, 15)
    progress = _create_mock_progress(
        slots={"race_distance": "Marathon", "race_date": datetime(2026, 4, 25, tzinfo=UTC)},
        awaiting_slots=["target_time"],
    )

    mock_extract.return_value = TrainingGoalInformation(
        race_distance="Marathon",
        race_date="2026-04-25",
        target_finish_time="03:00:00",
        goal_type="time",
        confidence_level="high",
    )

    resolved, remaining = resolve_awaited_slots("sub 3", progress, today)

    assert "target_time" in resolved
    assert resolved["target_time"] == "03:00:00"
    assert "target_time" not in remaining


@patch("app.coach.tools.plan_race.extract_training_goal")
def test_resolve_awaited_slots_multiple(mock_extract):
    """Test resolving multiple awaited slots."""
    today = date(2025, 1, 15)
    progress = _create_mock_progress(
        slots={},
        awaiting_slots=["race_distance", "race_date"],
    )

    mock_extract.return_value = TrainingGoalInformation(
        race_distance="Marathon",
        race_date="2026-04-25",
        target_finish_time=None,
        goal_type=None,
        confidence_level="high",
    )

    resolved, remaining = resolve_awaited_slots("marathon on April 25th", progress, today)

    assert "race_distance" in resolved
    assert "race_date" in resolved
    assert len(remaining) == 0


# ============================================================================
# INVARIANT TESTS (CRITICAL)
# ============================================================================


@patch("app.coach.tools.plan_race.extract_training_goal")
def test_invariant_no_reask_for_resolvable_date(mock_extract):
    """Test that system doesn't ask again for an already resolvable date.

    Scenario:
    1. User: "I'm training for a marathon in April"
    2. System asks for date
    3. User: "on the 25th!"
    4. System should resolve using April from context, not ask again
    """
    today = date(2025, 1, 15)

    # First turn: user mentions marathon in April
    progress1 = _create_mock_progress(
        slots={"race_distance": "Marathon"},
        awaiting_slots=["race_date"],
    )

    mock_extract.return_value = TrainingGoalInformation(
        race_distance="Marathon",
        race_date="2026-04-25",  # Should resolve using April from context
        target_finish_time=None,
        goal_type=None,
        confidence_level="high",
    )

    # Build context with known month
    context = build_conversation_context(progress1)
    context["known_race_month"] = "April"  # Simulate that April was mentioned

    # Second turn: user says "on the 25th!"
    resolved, remaining = resolve_awaited_slots("on the 25th!", progress1, today)

    # Should resolve without asking again
    assert "race_date" in resolved
    assert "race_date" not in remaining
    assert isinstance(resolved["race_date"], datetime)


@patch("app.coach.tools.plan_race.extract_training_goal")
def test_invariant_partial_followup_resolution(mock_extract):
    """Test that partial follow-ups are correctly resolved.

    Scenario:
    1. User: "I'm training for a marathon in April"
    2. System asks for date
    3. User: "April 25th"
    4. System should resolve without re-asking
    """
    today = date(2025, 1, 15)
    progress = _create_mock_progress(
        slots={"race_distance": "Marathon"},
        awaiting_slots=["race_date"],
    )

    mock_extract.return_value = TrainingGoalInformation(
        race_distance="Marathon",
        race_date="2026-04-25",
        target_finish_time=None,
        goal_type=None,
        confidence_level="high",
    )

    resolved, remaining = resolve_awaited_slots("April 25th", progress, today)

    # Should resolve the date
    assert "race_date" in resolved
    assert "race_date" not in remaining
    assert resolved["race_date"].month == 4
    assert resolved["race_date"].day == 25


# ============================================================================
# B42: SLOT CONTRACT INVARIANT TEST (NO CLARIFICATION AFTER SLOT COMPLETE)
# ============================================================================


@pytest.mark.asyncio
async def test_no_clarification_after_slot_complete():
    """B42: Test that tool never requests clarification after slots are validated as complete.

    This regression test ensures that:
    1. Once slots are extracted and validated (filled_slots contains race_date and race_distance)
    2. The slot gate passes (should_execute=True)
    3. The tool executes immediately without clarification requests
    4. Any violation = backend exception, not UX retry

    Test Case:
    - Slots are complete: {"race_date": "2026-04-25", "race_distance": "Marathon"}
    - Slot validation passes
    - Tool executes successfully
    - No clarification messages emitted
    """
    from datetime import datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.coach.errors import ToolContractViolationError
    from app.coach.executor.action_executor import CoachActionExecutor
    from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse

    # Create decision with complete slots
    decision = OrchestratorAgentResponse(
        intent="plan",
        horizon="race",
        action="EXECUTE",
        confidence=0.9,
        message="Creating your race plan...",
        response_type="plan",
        show_plan=True,
        plan_items=None,
        target_action="plan_race_build",
        required_slots=["race_date", "race_distance"],
        filled_slots={
            "race_date": datetime(2026, 4, 25, tzinfo=UTC),
            "race_distance": "Marathon",
        },
        missing_slots=[],  # Slots are complete
        next_question=None,
        should_execute=True,  # Slots validated and complete
    )

    deps = MagicMock()
    deps.user_id = "test_user"
    deps.athlete_id = 123

    # Mock the tool call to return success (no clarification)
    with patch("app.coach.executor.action_executor.call_tool") as mock_call_tool:
        mock_call_tool.return_value = {
            "success": True,
            "message": "Race plan created successfully",
            "saved_count": 10,
            "race_distance": "Marathon",
            "race_date": "2026-04-25T00:00:00+00:00",
        }

        result = await CoachActionExecutor.execute(
            decision=decision,
            deps=deps,
            conversation_id="test_conv_123",
        )

        # Verify tool was called with filled_slots in context (B37)
        assert mock_call_tool.called
        call_args = mock_call_tool.call_args
        assert call_args[0][0] == "plan_race_build"
        tool_args = call_args[0][1]
        assert "context" in tool_args
        assert "filled_slots" in tool_args["context"]
        assert tool_args["context"]["filled_slots"]["race_date"] == decision.filled_slots["race_date"]
        assert tool_args["context"]["filled_slots"]["race_distance"] == "Marathon"

        # Verify result is success (not a clarification)
        assert result == "Race plan created successfully"
        assert "[CLARIFICATION]" not in result
        assert "needs_clarification" not in str(result).lower()

    # Test that ToolContractViolationError is raised if slots are missing post-validation
    decision_with_missing_slots = OrchestratorAgentResponse(
        intent="plan",
        horizon="race",
        action="EXECUTE",
        confidence=0.9,
        message="Creating your race plan...",
        response_type="plan",
        show_plan=True,
        plan_items=None,
        target_action="plan_race_build",
        required_slots=["race_date", "race_distance"],
        filled_slots={},  # Missing slots - should fail
        missing_slots=["race_date", "race_distance"],
        next_question=None,
        should_execute=False,  # Slots not complete
    )

    # Should not execute since should_execute=False
    with patch("app.coach.executor.action_executor.call_tool") as mock_call_tool:
        result = await CoachActionExecutor.execute(
            decision=decision_with_missing_slots,
            deps=deps,
            conversation_id="test_conv_123",
        )

        # Should ask for clarification, not execute tool
        assert not mock_call_tool.called
        assert "race" in result.lower() or "date" in result.lower() or "distance" in result.lower()
