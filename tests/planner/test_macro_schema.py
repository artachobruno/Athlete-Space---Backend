"""Tests for macro plan schema validation.

Tests MacroPlanSchema to ensure:
- Intent is required and validated
- Race distance is optional but validated when present
- Week count and structure are validated
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.planner.enums import RaceDistance, TrainingIntent, WeekFocus
from app.planner.schemas import MacroPlanSchema, MacroWeekSchema


def test_macro_plan_schema_with_intent() -> None:
    """Test that macro plan schema accepts valid intent and race distance."""
    data = {
        "intent": "build",
        "race_distance": "marathon",
        "weeks": [
            {"week": 1, "focus": "base", "total_distance": 40.0}
        ]
    }
    schema = MacroPlanSchema.model_validate(data)
    assert schema.intent == TrainingIntent.BUILD
    assert schema.race_distance == RaceDistance.MARATHON
    assert len(schema.weeks) == 1
    assert schema.weeks[0].week == 1
    assert schema.weeks[0].focus == WeekFocus.BASE
    assert schema.weeks[0].total_distance == 40.0


def test_macro_plan_schema_season_no_race_distance() -> None:
    """Test that macro plan schema accepts season plan without race distance."""
    data = {
        "intent": "maintain",
        "race_distance": None,
        "weeks": [
            {"week": 1, "focus": "base", "total_distance": 30.0},
            {"week": 2, "focus": "build", "total_distance": 35.0}
        ]
    }
    schema = MacroPlanSchema.model_validate(data)
    assert schema.intent == TrainingIntent.MAINTAIN
    assert schema.race_distance is None
    assert len(schema.weeks) == 2


def test_macro_plan_schema_invalid_intent() -> None:
    """Test that invalid intent raises validation error."""
    data = {
        "intent": "invalid_intent",
        "race_distance": None,
        "weeks": [
            {"week": 1, "focus": "base", "total_distance": 30.0}
        ]
    }
    with pytest.raises(ValidationError):
        MacroPlanSchema.model_validate(data)


def test_macro_plan_schema_invalid_race_distance() -> None:
    """Test that invalid race distance raises validation error."""
    data = {
        "intent": "build",
        "race_distance": "invalid_distance",
        "weeks": [
            {"week": 1, "focus": "base", "total_distance": 40.0}
        ]
    }
    with pytest.raises(ValidationError):
        MacroPlanSchema.model_validate(data)


def test_macro_plan_schema_invalid_focus() -> None:
    """Test that invalid week focus raises validation error."""
    data = {
        "intent": "build",
        "race_distance": "marathon",
        "weeks": [
            {"week": 1, "focus": "invalid_focus", "total_distance": 40.0}
        ]
    }
    with pytest.raises(ValidationError):
        MacroPlanSchema.model_validate(data)


def test_macro_plan_schema_negative_distance() -> None:
    """Test that negative distance raises validation error."""
    data = {
        "intent": "build",
        "race_distance": "marathon",
        "weeks": [
            {"week": 1, "focus": "base", "total_distance": -10.0}
        ]
    }
    with pytest.raises(ValidationError):
        MacroPlanSchema.model_validate(data)


def test_macro_plan_schema_zero_distance() -> None:
    """Test that zero distance raises validation error."""
    data = {
        "intent": "build",
        "race_distance": "marathon",
        "weeks": [
            {"week": 1, "focus": "base", "total_distance": 0.0}
        ]
    }
    with pytest.raises(ValidationError):
        MacroPlanSchema.model_validate(data)


def test_macro_plan_schema_empty_weeks() -> None:
    """Test that empty weeks list raises validation error."""
    data = {
        "intent": "build",
        "race_distance": "marathon",
        "weeks": []
    }
    with pytest.raises(ValidationError):
        MacroPlanSchema.model_validate(data)


def test_macro_week_schema_week_less_than_one() -> None:
    """Test that week number less than 1 raises validation error."""
    data = {
        "intent": "build",
        "race_distance": "marathon",
        "weeks": [
            {"week": 0, "focus": "base", "total_distance": 40.0}
        ]
    }
    with pytest.raises(ValidationError):
        MacroPlanSchema.model_validate(data)
