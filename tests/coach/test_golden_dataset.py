"""Golden Dataset Test Runner.

Loads and validates against the golden dataset JSONL file.
Ensures all execution controller scenarios pass.
"""

import json
import pathlib

import pytest

from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.coach.validators.execution_validator import (
    validate_execution_controller_decision,
    validate_no_advice_before_execution,
    validate_no_chatty_response,
    validate_single_question,
)


def load_golden_dataset() -> list[dict]:
    """Load golden dataset from JSONL file.

    Returns:
        List of test cases as dictionaries
    """
    dataset_path = "tests/coach/golden_dataset.jsonl"
    test_cases = []

    try:
        with pathlib.Path(dataset_path).open(encoding="utf-8") as f:
            test_cases.extend(json.loads(line) for line in f if line.strip())
    except FileNotFoundError:
        pytest.skip(f"Golden dataset not found at {dataset_path}")

    return test_cases


@pytest.mark.parametrize("test_case", load_golden_dataset())
def test_golden_dataset_scenario(test_case: dict):
    """Test each scenario from golden dataset.

    Args:
        test_case: Test case dictionary from golden dataset
    """
    test_id = test_case.get("test_id", "unknown")
    test_name = test_case.get("name", "unnamed")
    expected = test_case.get("expected_llm_output", {})

    # Skip tests that are expected to fail (hard failure guards)
    # These are tested separately in test_7_x_* functions
    if test_case.get("expected_system_action") in {
        "REJECT_ADVICE",
        "REJECT_MULTIPLE_QUESTIONS",
        "REJECT_CHATTY",
    }:
        pytest.skip(f"Test {test_id} ({test_name}) is a failure guard test - tested separately")

    # Handle null user_facing_response (execution cases)
    user_facing_response = test_case.get("user_facing_response")
    if user_facing_response is None:
        # Execution case - message can be execution-related
        user_facing_response = expected.get("next_question", "Executing...")

    # Create decision object from expected output
    decision = OrchestratorAgentResponse(
        intent=expected.get("intent", "plan"),
        horizon=expected.get("horizon", "race"),
        action="EXECUTE" if expected.get("should_execute") else "NO_ACTION",
        confidence=0.9,
        message=user_facing_response or "",
        response_type=expected.get("response_type", "question"),
        target_action=expected.get("target_action"),
        required_slots=expected.get("required_slots", []),
        filled_slots=expected.get("filled_slots", {}),
        missing_slots=expected.get("missing_slots", []),
        next_question=expected.get("next_question"),
        should_execute=expected.get("should_execute", False),
    )

    # Validate execution controller rules
    is_valid, errors = validate_execution_controller_decision(decision)

    # Check must_not_contain if specified
    must_not_contain = test_case.get("must_not_contain", [])
    if must_not_contain:
        message_lower = decision.message.lower()
        found = [keyword for keyword in must_not_contain if keyword in message_lower]
        assert not found, f"Message contains forbidden keywords: {found}"

    # Assert validation passes
    assert is_valid, f"Test {test_id} ({test_name}) failed validation: {errors}"

    # Assert expected system action
    expected_action = test_case.get("expected_system_action")
    if expected_action == "EXECUTE":
        assert decision.should_execute, f"Test {test_id}: should_execute must be True"
        assert decision.action == "EXECUTE", f"Test {test_id}: action must be EXECUTE"
        assert not decision.missing_slots, f"Test {test_id}: cannot execute with missing slots"
    elif expected_action == "ASK_FOR_SLOT":
        assert decision.missing_slots, f"Test {test_id}: must have missing slots"
        assert decision.next_question or "?" in decision.message, f"Test {test_id}: must ask a question"
        assert not decision.should_execute, f"Test {test_id}: should_execute must be False"
    elif expected_action == "ASK_FOR_CONFIRMATION":
        # Confirmation cases might have slots complete but need confirmation
        # This is a special case - allow it for now if explicitly expected
        assert decision.next_question or "?" in decision.message, f"Test {test_id}: must ask a question"


def test_7_1_advice_before_execution_fails():
    """Test 7.1: Advice before execution must fail."""
    decision = OrchestratorAgentResponse(
        intent="plan",
        horizon="race",
        action="NO_ACTION",
        confidence=0.9,
        message="You should build mileage gradually. What's your race date?",
        response_type="question",
        target_action="plan_race_build",
        missing_slots=["race_date"],
        should_execute=False,
    )

    is_valid, errors = validate_execution_controller_decision(decision)
    assert not is_valid, "Advice before execution should fail validation"
    assert any("advice" in error.lower() for error in errors), "Should detect advice violation"


def test_7_2_multiple_questions_fails():
    """Test 7.2: Multiple questions must fail."""
    decision = OrchestratorAgentResponse(
        intent="plan",
        horizon="race",
        action="NO_ACTION",
        confidence=0.9,
        message="What's your race date and current mileage?",
        response_type="question",
        target_action="plan_race_build",
        missing_slots=["race_date", "current_mileage"],
        should_execute=False,
    )

    is_valid, errors = validate_execution_controller_decision(decision)
    assert not is_valid, "Multiple questions should fail validation"
    assert any("question" in error.lower() for error in errors), "Should detect multiple questions"


def test_7_3_chatty_response_fails():
    """Test 7.3: Chatty response must fail."""
    decision = OrchestratorAgentResponse(
        intent="plan",
        horizon="race",
        action="NO_ACTION",
        confidence=0.9,
        message="Let's start by thinking about your goals. What's your race date?",
        response_type="question",
        target_action="plan_race_build",
        missing_slots=["race_date"],
        should_execute=False,
    )

    is_valid, errors = validate_execution_controller_decision(decision)
    assert not is_valid, "Chatty response should fail validation"
    assert any("chatty" in error.lower() for error in errors), "Should detect chatty response"


def test_core_invariant_every_message_fills_asks_or_executes():
    """Core invariant test: Every message must fill slot, ask for slot, or trigger execution."""
    # Case 1: Ask for slot (valid)
    decision_ask = OrchestratorAgentResponse(
        intent="plan",
        horizon="race",
        action="NO_ACTION",
        confidence=0.9,
        message="What is the date of your marathon?",
        response_type="question",
        target_action="plan_race_build",
        missing_slots=["race_date"],
        next_question="What is the date of your marathon?",
        should_execute=False,
    )
    is_valid, _ = validate_execution_controller_decision(decision_ask)
    assert is_valid, "Asking for slot should be valid"

    # Case 2: Trigger execution (valid)
    decision_execute = OrchestratorAgentResponse(
        intent="plan",
        horizon="race",
        action="EXECUTE",
        confidence=0.9,
        message="Building your plan...",
        response_type="plan",
        target_action="plan_race_build",
        missing_slots=[],
        should_execute=True,
    )
    is_valid, _ = validate_execution_controller_decision(decision_execute)
    assert is_valid, "Executing when slots complete should be valid"

    # Case 3: No executable action (valid - informational)
    decision_info = OrchestratorAgentResponse(
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
    is_valid, _ = validate_execution_controller_decision(decision_info)
    assert is_valid, "Informational response with no action should be valid"
