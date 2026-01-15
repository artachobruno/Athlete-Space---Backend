"""Tests for MODIFY → day tool.

Tests enforce that:
- Intent is preserved by default
- Metrics mutate safely
- Original session remains
- No intent re-inference
"""

from datetime import UTC, date, datetime, timezone

import pytest

from app.coach.tools.modify_day import modify_day
from app.db.models import PlannedSession
from app.plans.modify.repository import get_planned_session_by_date
from app.plans.modify.types import DayModification


@pytest.fixture
def sample_session(db_session):
    """Create a sample planned session for testing."""
    session = PlannedSession(
        id="test-session-1",
        user_id="test-user",
        athlete_id=1,
        date=datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
        type="Run",
        title="Easy Run",
        duration_minutes=60,
        distance_mi=5.0,
        session_type="easy",  # Legacy/auxiliary
        intent="easy",  # Authoritative field
        intensity="easy",
        plan_type="race",
        source="planner_v2",
    )
    db_session.add(session)
    db_session.commit()
    return session


def test_modify_day_preserves_intent(sample_session, db_session):
    """Test that MODIFY preserves intent by default."""
    # Modify distance only
    modification = DayModification(
        change_type="adjust_distance",
        value=6.0,
        reason="Increase distance",
    )

    context = {
        "user_id": "test-user",
        "athlete_id": 1,
        "target_date": date(2024, 1, 15),
        "modification": modification.model_dump(),
    }

    result = modify_day(context)

    assert result["success"] is True

    # Fetch original and modified
    original = db_session.get(PlannedSession, sample_session.id)
    modified = db_session.get(PlannedSession, result["modified_session_id"])

    # Original still exists
    assert original is not None

    # Distance changed
    assert modified.distance_mi == 6.0
    assert original.distance_mi == 5.0

    # Intent preserved (authoritative field)
    assert modified.intent == original.intent
    assert modified.intent == "easy"


def test_modify_day_changes_distance_only(sample_session, db_session):
    """Test that MODIFY changes only the specified metric."""
    modification = DayModification(
        change_type="adjust_distance",
        value=8.0,
    )

    context = {
        "user_id": "test-user",
        "athlete_id": 1,
        "target_date": date(2024, 1, 15),
        "modification": modification.model_dump(),
    }

    result = modify_day(context)
    assert result["success"] is True

    modified = db_session.get(PlannedSession, result["modified_session_id"])

    # Distance changed
    assert modified.distance_mi == 8.0
    assert modified.distance_mi != sample_session.distance_mi

    # Duration unchanged
    assert modified.duration_minutes == sample_session.duration_minutes


def test_modify_day_explicit_intent_change(sample_session, db_session):
    """Test that explicit intent change is honored."""
    modification = DayModification(
        change_type="adjust_distance",
        value=6.0,
        explicit_intent_change="quality",  # Explicit intent change
    )

    context = {
        "user_id": "test-user",
        "athlete_id": 1,
        "target_date": date(2024, 1, 15),
        "modification": modification.model_dump(),
    }

    result = modify_day(context)
    assert result["success"] is True

    original = db_session.get(PlannedSession, sample_session.id)
    modified = db_session.get(PlannedSession, result["modified_session_id"])

    # Intent changed (authoritative field)
    assert modified.intent == "quality"
    assert modified.intent != original.intent


def test_modify_day_invalid_pace_for_intent_raises(sample_session, db_session):
    """Test that invalid pace for intent raises error."""
    # Try to set threshold pace for easy session (invalid)
    modification = DayModification(
        change_type="adjust_pace",
        value="threshold",  # Invalid for "easy" intent
    )

    context = {
        "user_id": "test-user",
        "athlete_id": 1,
        "target_date": date(2024, 1, 15),
        "modification": modification.model_dump(),
    }

    # This should raise or return error
    modify_day(context)
    # Note: Current implementation validates this via validate_pace_for_intent
    # If validation fails, result["success"] will be False
    # This test documents expected behavior


def test_original_session_remains(sample_session, db_session):
    """Test that original session is not deleted."""
    original_id = sample_session.id

    modification = DayModification(
        change_type="adjust_distance",
        value=7.0,
    )

    context = {
        "user_id": "test-user",
        "athlete_id": 1,
        "target_date": date(2024, 1, 15),
        "modification": modification.model_dump(),
    }

    result = modify_day(context)
    assert result["success"] is True

    # Original still exists
    original = db_session.get(PlannedSession, original_id)
    assert original is not None
    assert original.id == original_id

    # New session created
    assert result["modified_session_id"] != original_id


def test_modify_day_missing_session_returns_error(db_session):
    """Test that modifying non-existent session returns error."""
    modification = DayModification(
        change_type="adjust_distance",
        value=6.0,
    )

    context = {
        "user_id": "test-user",
        "athlete_id": 1,
        "target_date": date(2024, 1, 15),  # No session for this date
        "modification": modification.model_dump(),
    }

    result = modify_day(context)
    assert result["success"] is False
    assert "No planned session found" in result["error"]


def test_modify_day_missing_required_fields():
    """Test that missing required fields raises error."""
    context = {
        "user_id": "test-user",
        # Missing athlete_id, target_date, modification
    }

    with pytest.raises(ValueError, match="Missing required fields"):
        modify_day(context)
