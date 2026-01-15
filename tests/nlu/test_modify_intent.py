"""Tests for MODIFY intent classification."""

import pytest

from app.agents.nlu.postprocess import apply_disambiguation_rules
from app.agents.nlu.types import NLUResult


def test_modify_week():
    """Test MODIFY intent for week horizon."""
    result = NLUResult(intent="plan", horizon="week", slots={})
    result = apply_disambiguation_rules(result, "Reduce this week's volume")
    assert result.intent == "modify"
    assert result.horizon == "week"


def test_modify_day():
    """Test MODIFY intent for day horizon."""
    result = NLUResult(intent="plan", horizon="day", slots={})
    result = apply_disambiguation_rules(result, "Move my workout to tomorrow")
    assert result.intent == "modify"
    assert result.horizon == "day"


def test_modify_season():
    """Test MODIFY intent for season horizon."""
    result = NLUResult(intent="plan", horizon="season", slots={})
    result = apply_disambiguation_rules(result, "I got injured, adjust my season")
    assert result.intent == "modify"
    assert result.horizon == "season"


def test_modify_race():
    """Test MODIFY intent for race horizon."""
    result = NLUResult(intent="plan", horizon="race", slots={})
    result = apply_disambiguation_rules(result, "My race date changed")
    assert result.intent == "modify"
    assert result.horizon == "race"


def test_modify_verbs():
    """Test that all MODIFY verbs trigger correct classification."""
    modify_phrases = [
        "Change my workout",
        "Move my session",
        "Reduce volume",
        "Increase intensity",
        "Adjust my plan",
        "Replace this workout",
        "Swap sessions",
        "Delete tomorrow's workout",
        "Add a rest day",
    ]

    for phrase in modify_phrases:
        result = NLUResult(intent="plan", horizon="week", slots={})
        result = apply_disambiguation_rules(result, phrase)
        assert result.intent == "modify", f"Failed for phrase: {phrase}"


def test_plan_not_modified():
    """Test that PLAN intent is preserved when no modify verbs are present."""
    result = NLUResult(intent="plan", horizon="week", slots={})
    result = apply_disambiguation_rules(result, "Plan my week")
    assert result.intent == "plan"
    assert result.horizon == "week"
