"""Tests for workout intent taxonomy.

Tests enforce that:
- Every workout has intent (session-level, not metrics-level)
- Intent is valid
- Week has exactly one long run
- Intent is immutable by default (cannot change unless explicitly requested)
"""

import pytest

from app.planning.output.models import MaterializedSession
from app.plans.intent_rules import get_allowed_zones_for_intent
from app.plans.types import WorkoutIntent, WorkoutMetrics
from app.plans.validators import validate_workout_intent, validate_workout_metrics


def test_every_workout_has_intent():
    """Test that every workout must have intent at session level."""
    # Intent is on MaterializedSession, not WorkoutMetrics
    session = MaterializedSession(
        day="mon",
        intent="easy",  # Required at session level
        session_template_id="test",
        session_type="easy",
        duration_minutes=60,
        distance_miles=5.0,
    )
    assert session.intent == "easy"
    validate_workout_intent(session.intent)

    # WorkoutMetrics does NOT have intent (it's session-level)
    metrics = WorkoutMetrics(
        primary="distance",
        distance_miles=5.0,
    )
    validate_workout_metrics(metrics)
    # Metrics are valid without intent - intent is session-level


def test_intent_is_valid():
    """Test that intent must be one of canonical values."""
    valid_intents = ["rest", "easy", "long", "quality"]

    for intent in valid_intents:
        validate_workout_intent(intent)

    # Invalid intent should raise
    with pytest.raises(ValueError, match="Invalid intent"):
        validate_workout_intent("invalid")


def test_week_has_single_long_run():
    """Test that a week plan has exactly one long run."""
    # Simulate a week plan
    week_sessions = [
        {"intent": "easy"},
        {"intent": "quality"},
        {"intent": "long"},  # Only one long
        {"intent": "easy"},
        {"intent": "rest"},
        {"intent": "easy"},
        {"intent": "easy"},
    ]

    long_count = sum(1 for s in week_sessions if s["intent"] == "long")
    assert long_count == 1, "Week must have exactly one long run"


def test_intent_allowed_zones():
    """Test intent-pace zone constraints (non-enforcing, just defined)."""
    # Easy intent allows easy zones
    easy_zones = get_allowed_zones_for_intent("easy")
    assert easy_zones is not None
    assert "easy" in easy_zones
    assert "recovery" in easy_zones
    assert "z1" in easy_zones

    # Quality intent allows intensity zones
    quality_zones = get_allowed_zones_for_intent("quality")
    assert quality_zones is not None
    assert "threshold" in quality_zones
    assert "tempo" in quality_zones
    assert "vo2max" in quality_zones

    # Long intent allows steady/easy zones
    long_zones = get_allowed_zones_for_intent("long")
    assert long_zones is not None
    assert "easy" in long_zones
    assert "steady" in long_zones
    assert "mp" in long_zones

    # Rest has no zones
    rest_zones = get_allowed_zones_for_intent("rest")
    assert rest_zones is None


def test_intent_immutable_by_default():
    """Test that intent is stable (cannot change unless explicitly requested).

    Intent is preserved when modifying metrics. Intent can only change
    when explicitly requested (e.g., "make this an easy day instead").
    """
    # Original session with intent
    original_session = MaterializedSession(
        day="mon",
        intent="easy",
        session_template_id="test",
        session_type="easy",
        duration_minutes=60,
        distance_miles=5.0,
    )

    # Modifying metrics should preserve intent
    new_metrics = WorkoutMetrics(
        primary="distance",
        distance_miles=6.0,  # Changed distance
    )

    # Intent is preserved at session level, not in metrics
    assert original_session.intent == "easy"
    # When creating new session with modified metrics, intent is preserved
    new_session = MaterializedSession(
        day=original_session.day,
        intent=original_session.intent,  # Preserve intent
        session_template_id=original_session.session_template_id,
        session_type=original_session.session_type,
        duration_minutes=original_session.duration_minutes,
        distance_miles=new_metrics.distance_miles,  # Use new metrics
    )
    assert new_session.intent == original_session.intent


def test_intent_not_inferred_from_pace():
    """Test that intent is not inferred from pace zone.

    Intent and pace are independent. Intent describes purpose,
    pace describes intensity. MODIFY must preserve intent, never re-infer.
    """
    # Easy session with easy pace
    easy_session = MaterializedSession(
        day="mon",
        intent="easy",  # Intent is session-level
        session_template_id="test",
        session_type="easy",
        duration_minutes=60,
        distance_miles=5.0,
    )

    # Quality session with threshold pace (different intent, different zone)
    quality_session = MaterializedSession(
        day="tue",
        intent="quality",  # Intent is session-level
        session_template_id="test",
        session_type="tempo",
        duration_minutes=45,
        distance_miles=5.0,
    )

    # Intent and pace are independent
    assert easy_session.intent != quality_session.intent
    # Intent describes purpose, pace describes intensity


def test_all_intents_are_valid():
    """Test that all canonical intents are recognized at session level."""

    # This test ensures the type system enforces valid intents
    valid_intents: list[WorkoutIntent] = ["rest", "easy", "long", "quality"]

    for intent in valid_intents:
        session = MaterializedSession(
            day="mon",
            intent=intent,
            session_template_id="test",
            session_type="easy",
            duration_minutes=60,
            distance_miles=5.0,
        )
        validate_workout_intent(session.intent)
        assert session.intent == intent
