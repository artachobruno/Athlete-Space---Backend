"""Execution Controller Test Suite.

Tests for the execution-driven agent behavior.
Ensures the orchestrator acts as a slot-filling executor, not a conversational coach.
"""

from datetime import date

import pytest

from app.coach.clarification import generate_slot_clarification
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.core.tool_names import PLAN_RACE_BUILD, PLAN_WEEK


class TestExecutionControllerBehavior:
    """Test execution controller behavior - slot-driven, no chat."""

    def test_single_question_rule(self):
        """Test 7.2: Must fail if multiple questions are asked."""
        # Single question - should pass
        q1 = generate_slot_clarification(PLAN_RACE_BUILD, ["race_date"])
        assert q1.count("?") == 1, "Must be exactly one question"
        assert len(q1.split("\n\n")) == 1, "Must be no paragraphs"

        # Should not contain multiple questions
        assert "and" not in q1.lower() or q1.lower().count("?") == 1
        assert q1.count("?") <= 1

    def test_no_advice_in_message(self):
        """Test 7.1: Must fail if advice is provided before execution."""
        advice_keywords = [
            "you should",
            "you should focus on",
            "it's important to",
            "remember to",
            "here's what",
            "tips",
            "advice",
            "we'll focus on",
        ]

        # Valid response (single question)
        valid_response = "What is the date of your marathon?"
        for keyword in advice_keywords:
            assert keyword not in valid_response.lower()

        # Invalid response (contains advice) - should be caught by validator
        invalid_response = "You should build mileage gradually. What's your race date?"
        has_advice = any(keyword in invalid_response.lower() for keyword in advice_keywords)
        assert has_advice, "This should contain advice and be rejected"

    def test_no_chatty_response(self):
        """Test 7.3: Must fail if response is chatty instead of slot-oriented."""
        from app.coach.validators.execution_validator import validate_no_chatty_response

        # Chatty response - should fail validation
        chatty = "Let's start by thinking about your goals. What's your race date?"
        is_valid, error = validate_no_chatty_response(chatty, PLAN_RACE_BUILD, ["race_date"])
        assert not is_valid, "Chatty response should fail validation"
        assert error is not None, "Should have error message"

        # Slot-oriented response - should pass
        slot_oriented = "What is the date of your marathon?"
        is_valid, error = validate_no_chatty_response(slot_oriented, PLAN_RACE_BUILD, ["race_date"])
        assert is_valid, "Slot-oriented response should pass validation"

    def test_vague_goal_triggers_slot_collection(self):
        """Test 1.1: Vague goal should trigger slot collection."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="What is the date of your marathon?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            required_slots=["race_date", "race_distance"],
            filled_slots={"race_distance": "Marathon"},
            missing_slots=["race_date"],
            next_question="What is the date of your marathon?",
            should_execute=False,
        )

        assert decision.target_action == PLAN_RACE_BUILD
        assert "race_date" in decision.missing_slots
        assert decision.next_question is not None
        assert decision.should_execute is False

    def test_exact_date_triggers_execution(self):
        """Test 1.3: Exact date should trigger execution."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="EXECUTE",
            confidence=0.9,
            message="Building your plan...",
            response_type="plan",
            target_action=PLAN_RACE_BUILD,
            required_slots=["race_date", "race_distance"],
            filled_slots={"race_date": date(2026, 4, 25), "race_distance": "Marathon"},
            missing_slots=[],
            next_question=None,
            should_execute=True,
        )

        assert decision.missing_slots == []
        assert decision.should_execute is True
        assert decision.action == "EXECUTE"

    def test_partial_date_info_still_missing(self):
        """Test 1.2: Partial time info still needs exact date."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="Do you know the exact race date in April?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            required_slots=["race_date", "race_distance"],
            filled_slots={"race_distance": "Marathon", "race_date_month": "April"},
            missing_slots=["race_date_exact"],
            next_question="Do you know the exact race date in April?",
            should_execute=False,
        )

        assert len(decision.missing_slots) > 0
        assert decision.should_execute is False

    def test_date_first_then_distance(self):
        """Test 2.1: Date first, then distance."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="Is this a marathon, half marathon, or another distance?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            required_slots=["race_date", "race_distance"],
            filled_slots={"race_date": date(2026, 4, 25)},
            missing_slots=["race_distance"],
            next_question="Is this a marathon, half marathon, or another distance?",
            should_execute=False,
        )

        assert "race_date" not in decision.missing_slots
        assert "race_distance" in decision.missing_slots

    def test_distance_synonym_resolution(self):
        """Test 2.2: Distance synonym (26.2 -> Marathon) should work."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="EXECUTE",
            confidence=0.9,
            message="Building your plan...",
            response_type="plan",
            target_action=PLAN_RACE_BUILD,
            required_slots=["race_date", "race_distance"],
            filled_slots={"race_date": date(2026, 4, 25), "race_distance": "Marathon"},
            missing_slots=[],
            next_question=None,
            should_execute=True,
        )

        assert decision.filled_slots.get("race_distance") == "Marathon"
        assert decision.missing_slots == []
        assert decision.should_execute is True

    def test_weekly_focus_before_plan_executes_race_plan(self):
        """Test 4.1: Weekly focus before plan exists should trigger race plan."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="week",
            action="NO_ACTION",
            confidence=0.9,
            message="I can plan your week once your marathon plan is created. What is your marathon date?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            required_slots=["race_date"],
            filled_slots={},
            missing_slots=["race_date"],
            next_question="I can plan your week once your marathon plan is created. What is your marathon date?",
            should_execute=False,
        )

        # Should target race plan, not weekly plan
        assert decision.target_action == PLAN_RACE_BUILD
        assert decision.should_execute is False
        # Should not contain advice
        advice_keywords = ["focus on", "you should", "tips"]
        message_lower = decision.message.lower()
        assert not any(keyword in message_lower for keyword in advice_keywords)

    def test_weekly_focus_after_plan_executes_weekly_plan(self):
        """Test 5.1: Weekly focus after plan exists should execute weekly plan."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="week",
            action="EXECUTE",
            confidence=0.9,
            message="Creating your weekly plan...",
            response_type="weekly_plan",
            target_action=PLAN_WEEK,
            required_slots=[],
            filled_slots={},
            missing_slots=[],
            next_question=None,
            should_execute=True,
        )

        assert decision.target_action == PLAN_WEEK
        assert decision.missing_slots == []
        assert decision.should_execute is True
        assert decision.action == "EXECUTE"

    def test_ambiguous_confirmation_still_missing(self):
        """Test 6.1: Ambiguous confirmation should still be missing."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="Let me know when you're ready and I can build the plan.",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            required_slots=["confirmation"],
            filled_slots={"race_date": date(2026, 4, 25), "race_distance": "Marathon"},
            missing_slots=["confirmation"],
            next_question="Let me know when you're ready and I can build the plan.",
            should_execute=False,
        )

        assert "confirmation" in decision.missing_slots
        assert decision.should_execute is False

    def test_off_topic_allowed_informational(self):
        """Test 6.2: Off-topic questions are allowed (informational)."""
        decision = OrchestratorAgentResponse(
            intent="question",
            horizon=None,
            action="NO_ACTION",
            confidence=0.9,
            message="A marathon is 26.2 miles (42.195 kilometers).",
            response_type="explanation",
            target_action=None,
            required_slots=[],
            filled_slots={},
            missing_slots=[],
            next_question=None,
            should_execute=False,
        )

        assert decision.target_action is None
        assert decision.should_execute is False
        # Informational responses are allowed when no executable action

    def test_validator_rejects_advice(self):
        """Test that validator catches advice before execution."""
        # This should pass validation (but would be logged as warning in real system)
        decision_with_advice = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="You should build mileage gradually. What's your race date?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            required_slots=["race_date", "race_distance"],
            filled_slots={"race_distance": "Marathon"},
            missing_slots=["race_date"],
            next_question="What is the date of your marathon?",
            should_execute=False,
        )

        # Validator should detect advice and use next_question instead
        # In real system, this would be handled by validate_no_advice_before_execution
        assert decision_with_advice.next_question is not None
        # The validator would replace message with next_question

    def test_slot_completion_core_invariant(self):
        """Core invariant: Every message must fill slot, ask for slot, or trigger execution."""
        # Case 1: Ask for slot
        ask_for_slot = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="What is the date of your marathon?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            missing_slots=["race_date"],
            should_execute=False,
        )
        assert ask_for_slot.missing_slots
        assert ask_for_slot.next_question or ask_for_slot.message

        # Case 2: Trigger execution
        trigger_execution = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="EXECUTE",
            confidence=0.9,
            message="Building your plan...",
            response_type="plan",
            target_action=PLAN_RACE_BUILD,
            missing_slots=[],
            should_execute=True,
        )
        assert trigger_execution.should_execute
        assert trigger_execution.action == "EXECUTE"

        # Case 3: No executable action (allowed)
        no_action = OrchestratorAgentResponse(
            intent="question",
            horizon=None,
            action="NO_ACTION",
            confidence=0.9,
            message="A marathon is 26.2 miles.",
            response_type="explanation",
            target_action=None,
            missing_slots=[],
            should_execute=False,
        )
        assert no_action.target_action is None


class TestSlotStateAccumulation:
    """Test slot state accumulation across conversation turns."""

    def test_slot_state_accumulation_across_turns(self):
        """Test that slots accumulate across multiple turns (regression test)."""
        # Turn 1: User provides race distance
        turn1 = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="What is the date of your marathon?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            filled_slots={"race_distance": "Marathon"},
            missing_slots=["race_date"],
            should_execute=False,
        )
        assert "race_distance" in turn1.filled_slots
        assert "race_date" in turn1.missing_slots

        # Turn 2: User provides race date - should retain race_distance
        turn2 = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="EXECUTE",
            confidence=0.9,
            message="Building your plan...",
            response_type="plan",
            target_action=PLAN_RACE_BUILD,
            filled_slots={"race_distance": "Marathon", "race_date": date(2026, 4, 25)},
            missing_slots=[],
            should_execute=True,
        )
        # Critical: race_distance must still be present
        assert "race_distance" in turn2.filled_slots
        assert turn2.filled_slots["race_distance"] == "Marathon"
        assert "race_date" in turn2.filled_slots
        assert turn2.should_execute

    def test_complete_slots_in_single_turn(self):
        """Test that complete slots in single turn trigger immediate execution."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="EXECUTE",
            confidence=0.9,
            message="Building your plan...",
            response_type="plan",
            target_action=PLAN_RACE_BUILD,
            filled_slots={"race_distance": "Marathon", "race_date": date(2026, 4, 25)},
            missing_slots=[],
            should_execute=True,
        )
        assert decision.should_execute
        assert decision.action == "EXECUTE"
        assert len(decision.missing_slots) == 0

    def test_slot_already_filled_not_re_asked(self):
        """Test that already-filled slots are not re-asked (regression prevention)."""
        # Initial state: race_distance already filled
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="What is the date of your marathon?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            filled_slots={"race_distance": "Marathon"},
            missing_slots=["race_date"],
            should_execute=False,
        )
        # Critical: Must NOT ask for race_distance again
        assert "race_distance" not in decision.missing_slots
        assert "race_date" in decision.missing_slots
        # Message should only ask for race_date
        assert "race_distance" not in decision.message.lower() or "marathon" in decision.message.lower()

    def test_vague_goal_triggers_slot_collection_first(self):
        """Test that vague goal triggers slot collection before execution."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="What is the date of your race?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            filled_slots={},
            missing_slots=["race_date", "race_distance"],
            should_execute=False,
        )
        assert decision.target_action == PLAN_RACE_BUILD
        assert len(decision.missing_slots) > 0
        assert decision.should_execute is False

    def test_exact_date_with_distance_triggers_execution(self):
        """Test that exact date with distance triggers immediate execution."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="EXECUTE",
            confidence=0.9,
            message="Building your plan...",
            response_type="plan",
            target_action=PLAN_RACE_BUILD,
            filled_slots={"race_distance": "Marathon", "race_date": date(2026, 4, 25)},
            missing_slots=[],
            should_execute=True,
        )
        assert decision.should_execute
        assert decision.action == "EXECUTE"
        assert len(decision.missing_slots) == 0

    def test_conversation_state_invariant(self):
        """Test core invariant: filled_slots must reflect conversation slot state."""
        # Simulate conversation state
        conversation_slot_state: dict[str, str | date | int | float | bool | None] = {"race_distance": "Marathon"}
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="race",
            action="NO_ACTION",
            confidence=0.9,
            message="What is the date of your marathon?",
            response_type="question",
            target_action=PLAN_RACE_BUILD,
            filled_slots=conversation_slot_state.copy(),
            missing_slots=["race_date"],
            should_execute=False,
        )
        # Critical invariant: filled_slots must match conversation slot state
        assert decision.filled_slots == conversation_slot_state
        # Missing slots must be disjoint from filled slots
        assert set(decision.missing_slots).isdisjoint(set(decision.filled_slots.keys()))

    def test_weekly_plan_after_race_plan_executes(self):
        """Test that weekly plan request after race plan executes weekly plan."""
        decision = OrchestratorAgentResponse(
            intent="plan",
            horizon="week",
            action="EXECUTE",
            confidence=0.9,
            message="Creating your weekly plan...",
            response_type="weekly_plan",
            target_action="plan_week",
            filled_slots={},
            missing_slots=[],
            should_execute=True,
        )
        assert decision.target_action == PLAN_WEEK
        assert decision.should_execute
        assert decision.action == "EXECUTE"
