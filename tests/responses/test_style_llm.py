"""Tests for Style LLM layer."""

import pytest

from app.coach.schemas.athlete_state import AthleteState
from app.coach.schemas.orchestrator_response import OrchestratorAgentResponse
from app.responses.input_builder import build_style_input
from app.responses.prompts import StyleLLMInput
from app.responses.style_llm import generate_coach_message
from app.responses.validator import validate_message


@pytest.mark.asyncio
async def test_golden_example():
    """Test golden example from spec."""
    structured_input: StyleLLMInput = {
        "goal": "marathon build",
        "headline": "Your marathon build is on track",
        "situation": "You're mid-block with manageable fatigue",
        "signal": "training stress balance (TSB) of -3.5 with a stable load trend",
        "action": "No changes recommended",
        "next": "Let's reassess after your long run",
    }

    message = await generate_coach_message(structured_input)

    # Validate output
    validate_message(message)

    # Check characteristics
    sentences = [s for s in message.split(".") if s.strip()]
    assert 2 <= len(sentences) <= 4, f"Expected 2-4 sentences, got {len(sentences)}"

    # Check for goal anchor
    assert "marathon" in message.lower() or "build" in message.lower()

    # Check for one metric (TSB mentioned)
    assert "tsb" in message.lower() or "training stress balance" in message.lower()

    # Check for positive framing
    assert "no change" in message.lower() or "stay" in message.lower() or "continue" in message.lower()

    # Check for CTA
    assert "reassess" in message.lower() or "long run" in message.lower()


@pytest.mark.asyncio
async def test_golden_example_without_headline():
    """Test that Style LLM works without headline (optional field)."""
    structured_input: StyleLLMInput = {
        "goal": "marathon build",
        "headline": None,
        "situation": "You're mid-block with manageable fatigue",
        "signal": "training stress balance (TSB) of -3.5 with a stable load trend",
        "action": "No changes recommended",
        "next": "Let's reassess after your long run",
    }

    message = await generate_coach_message(structured_input)

    # Validate output
    validate_message(message)

    # Check characteristics
    sentences = [s for s in message.split(".") if s.strip()]
    assert 2 <= len(sentences) <= 4, f"Expected 2-4 sentences, got {len(sentences)}"

    # Should still work without explicit headline
    assert len(message) > 0

    message = await generate_coach_message(structured_input)

    # Validate output
    validate_message(message)

    # Check characteristics
    sentences = [s for s in message.split(".") if s.strip()]
    assert 2 <= len(sentences) <= 4, f"Expected 2-4 sentences, got {len(sentences)}"

    # Check for goal anchor
    assert "marathon" in message.lower() or "build" in message.lower()

    # Check for one metric (TSB mentioned)
    assert "tsb" in message.lower() or "training stress balance" in message.lower()

    # Check for positive framing
    assert "no change" in message.lower() or "stay" in message.lower() or "continue" in message.lower()

    # Check for CTA
    assert "reassess" in message.lower() or "long run" in message.lower()


def test_validator_rejects_too_many_sentences():
    """Test validator rejects messages with too many sentences."""
    # Create a message with 5+ sentences
    long_message = "Sentence one. Sentence two. Sentence three. Sentence four. Sentence five."

    with pytest.raises(ValueError, match="Too many sentences"):
        validate_message(long_message)


def test_validator_rejects_metric_dumps():
    """Test validator rejects metric dumps."""
    # Message with multiple metrics (multiple numbers and dashes)
    metric_dump = "CTL: 30.4, ATL: 0.6, TSB: 30.4, Load: falling, Volatility: low"

    with pytest.raises(ValueError, match="Too many metrics"):
        validate_message(metric_dump)


def test_validator_rejects_too_many_numeric_characters():
    """Test validator rejects messages with too many numeric characters."""
    # Message with many numbers (dashboard-like output)
    dashboard_output = "CTL: 30.4, ATL: 0.6, TSB: 30.4, Volume: 22.9h, 14-day: 50.0h"

    with pytest.raises(ValueError, match="Too many numeric characters"):
        validate_message(dashboard_output)


def test_validator_rejects_forbidden_wording():
    """Test validator rejects forbidden wording."""
    # Test each forbidden word
    forbidden_messages = [
        "You should reduce volume",
        "You must rest",
        "I changed your plan",
        "I updated the schedule",
    ]

    for message in forbidden_messages:
        with pytest.raises(ValueError, match="Forbidden wording"):
            validate_message(message)


def test_validator_accepts_valid_messages():
    """Test validator accepts valid messages."""
    valid_messages = [
        "Your training is on track. Stay the course.",
        "Your marathon build is progressing well. You're carrying a manageable level of fatigue. Keep executing the plan.",
        "You're well-recovered and ready for quality work. Focus on your next key session.",
    ]

    for message in valid_messages:
        # Should not raise
        validate_message(message)


def test_build_style_input_from_executor_reply():
    """Test input builder extracts structured input from executor reply."""
    decision = OrchestratorAgentResponse(
        intent="explain",
        horizon=None,
        action="EXECUTE",
        confidence=0.9,
        message="Retrieving your current CTL, ATL, and TSB.",
        response_type="summary",
        show_plan=False,
        plan_items=None,
        structured_data={},
        follow_up=None,
        action_plan=None,
        target_action="explain_training_state",
        required_attributes=[],
        optional_attributes=[],
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=True,
        required_slots=[],
        next_executable_action=None,
        execution_confirmed=False,
    )

    executor_reply = "CTL: 30.4, ATL: 0.6, TSB: 30.4, Load trend: falling, Volatility: low, Days since rest: 61"

    athlete_state = AthleteState(
        ctl=30.4,
        atl=0.6,
        tsb=30.4,
        load_trend="falling",
        volatility="low",
        days_since_rest=61,
        days_to_race=None,
        seven_day_volume_hours=22.9,
        fourteen_day_volume_hours=50.0,
        flags=[],
        confidence=0.9,
    )

    style_input = build_style_input(decision, executor_reply, athlete_state)

    # Check structure
    assert "goal" in style_input
    assert "headline" in style_input
    assert "situation" in style_input
    assert "signal" in style_input
    assert "action" in style_input
    assert "next" in style_input

    # Check signal extraction (should have TSB)
    assert "tsb" in style_input["signal"].lower() or "training stress balance" in style_input["signal"].lower()

    # Check action
    assert isinstance(style_input["action"], str)
    assert len(style_input["action"]) > 0

    # Check CTA is always set (should default to "All good for now." if None)
    assert style_input["next"] is not None
    assert len(style_input["next"]) > 0


def test_build_style_input_no_athlete_state():
    """Test input builder works without athlete state."""
    decision = OrchestratorAgentResponse(
        intent="explain",
        horizon=None,
        action="EXECUTE",
        confidence=0.9,
        message="Retrieving your current CTL, ATL, and TSB.",
        response_type="summary",
        show_plan=False,
        plan_items=None,
        structured_data={},
        follow_up=None,
        action_plan=None,
        target_action="explain_training_state",
        required_attributes=[],
        optional_attributes=[],
        filled_slots={},
        missing_slots=[],
        next_question=None,
        should_execute=True,
        required_slots=[],
        next_executable_action=None,
        execution_confirmed=False,
    )

    executor_reply = "CTL: 30.4, ATL: 0.6, TSB: 30.4"

    style_input = build_style_input(decision, executor_reply, None)

    # Should still work
    assert "goal" in style_input
    assert "signal" in style_input

    # CTA should be set (defaults to "All good for now." if None)
    assert style_input["next"] is not None


def test_extract_single_metric_prioritizes_tsb():
    """Test that metric extraction prioritizes TSB."""
    from app.responses import input_builder

    reply = "CTL: 30.4, ATL: 0.6, TSB: 30.4, Load trend: falling"
    metric = input_builder._extract_single_metric(reply)

    assert metric is not None
    assert "tsb" in metric.lower() or "training stress balance" in metric.lower()
    assert "30.4" in metric


def test_extract_single_metric_falls_back_to_ctl():
    """Test metric extraction falls back to CTL if TSB not found."""
    from app.responses import input_builder

    reply = "CTL: 30.4, ATL: 0.6"
    metric = input_builder._extract_single_metric(reply)

    assert metric is not None
    assert "ctl" in metric.lower() or "chronic" in metric.lower()
    assert "30.4" in metric
