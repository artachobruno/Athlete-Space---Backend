"""Tests for revision diff engine.

Tests that the diff engine:
- Detects field changes correctly
- Identifies added sessions
- Identifies removed sessions
- Identifies unchanged sessions
- Handles multi-field changes
"""

from datetime import UTC, datetime, timezone

import pytest

from app.coach.diff.plan_diff import build_plan_diff
from app.db.models import PlannedSession


@pytest.fixture
def sample_session_1() -> PlannedSession:
    """Create a sample PlannedSession."""
    return PlannedSession(
        id="session-1",
        user_id="user-1",
        athlete_id=1,
        date=datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC),
        type="Run",
        title="Easy Run",
        distance_mi=5.0,
        duration_minutes=40,
        intent="easy",
        plan_type="race",
        source="planner_v2",
    )


@pytest.fixture
def sample_session_2() -> PlannedSession:
    """Create another sample PlannedSession."""
    return PlannedSession(
        id="session-2",
        user_id="user-1",
        athlete_id=1,
        date=datetime(2024, 6, 16, 0, 0, 0, tzinfo=UTC),
        type="Run",
        title="Long Run",
        distance_mi=10.0,
        duration_minutes=80,
        intent="long",
        plan_type="race",
        source="planner_v2",
    )


@pytest.fixture
def modified_session_1() -> PlannedSession:
    """Create a modified version of session_1 (distance changed)."""
    return PlannedSession(
        id="session-1",
        user_id="user-1",
        athlete_id=1,
        date=datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC),
        type="Run",
        title="Easy Run",
        distance_mi=6.0,  # Changed from 5.0
        duration_minutes=40,
        intent="easy",
        plan_type="race",
        source="planner_v2",
    )


def test_distance_change_detected(sample_session_1, modified_session_1):
    """Test that distance changes are detected."""
    diff = build_plan_diff(
        before_sessions=[sample_session_1],
        after_sessions=[modified_session_1],
        scope="day",
    )

    assert len(diff.modified) == 1
    assert diff.modified[0].session_id == "session-1"
    assert len(diff.modified[0].changes) == 1
    assert diff.modified[0].changes[0].field == "distance_mi"
    assert diff.modified[0].changes[0].before == 5.0
    assert diff.modified[0].changes[0].after == 6.0
    assert len(diff.added) == 0
    assert len(diff.removed) == 0
    assert len(diff.unchanged) == 0


def test_added_session_detected(sample_session_1, sample_session_2):
    """Test that added sessions are detected."""
    diff = build_plan_diff(
        before_sessions=[sample_session_1],
        after_sessions=[sample_session_1, sample_session_2],
        scope="week",
    )

    assert len(diff.added) == 1
    assert diff.added[0].session_id == "session-2"
    assert diff.added[0].type == "Run"
    assert diff.added[0].title == "Long Run"
    assert len(diff.removed) == 0
    assert len(diff.modified) == 0
    assert len(diff.unchanged) == 1
    assert diff.unchanged[0] == "session-1"


def test_removed_session_detected(sample_session_1, sample_session_2):
    """Test that removed sessions are detected."""
    diff = build_plan_diff(
        before_sessions=[sample_session_1, sample_session_2],
        after_sessions=[sample_session_1],
        scope="week",
    )

    assert len(diff.removed) == 1
    assert diff.removed[0].session_id == "session-2"
    assert diff.removed[0].type == "Run"
    assert diff.removed[0].title == "Long Run"
    assert len(diff.added) == 0
    assert len(diff.modified) == 0
    assert len(diff.unchanged) == 1
    assert diff.unchanged[0] == "session-1"


def test_unchanged_session_detected(sample_session_1):
    """Test that unchanged sessions are detected."""
    diff = build_plan_diff(
        before_sessions=[sample_session_1],
        after_sessions=[sample_session_1],
        scope="day",
    )

    assert len(diff.unchanged) == 1
    assert diff.unchanged[0] == "session-1"
    assert len(diff.added) == 0
    assert len(diff.removed) == 0
    assert len(diff.modified) == 0


def test_multi_field_change_detected(sample_session_1):
    """Test that multiple field changes are detected."""
    modified = PlannedSession(
        id="session-1",
        user_id="user-1",
        athlete_id=1,
        date=datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC),
        type="Run",
        title="Easy Run Modified",  # Changed
        distance_mi=7.0,  # Changed
        duration_minutes=50,  # Changed
        intent="easy",
        plan_type="race",
        source="planner_v2",
    )

    diff = build_plan_diff(
        before_sessions=[sample_session_1],
        after_sessions=[modified],
        scope="day",
    )

    assert len(diff.modified) == 1
    assert diff.modified[0].session_id == "session-1"
    assert len(diff.modified[0].changes) == 3

    change_fields = {change.field for change in diff.modified[0].changes}
    assert "title" in change_fields
    assert "distance_mi" in change_fields
    assert "duration_minutes" in change_fields


def test_empty_before_and_after():
    """Test diff with empty before and after."""
    diff = build_plan_diff(
        before_sessions=[],
        after_sessions=[],
        scope="plan",
    )

    assert len(diff.added) == 0
    assert len(diff.removed) == 0
    assert len(diff.modified) == 0
    assert len(diff.unchanged) == 0


def test_all_sessions_replaced(sample_session_1, sample_session_2):
    """Test when all sessions are replaced (removed + added)."""
    new_session_1 = PlannedSession(
        id="session-3",
        user_id="user-1",
        athlete_id=1,
        date=datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC),
        type="Run",
        title="New Run",
        distance_mi=8.0,
        duration_minutes=60,
        intent="easy",
        plan_type="race",
        source="planner_v2",
    )

    diff = build_plan_diff(
        before_sessions=[sample_session_1, sample_session_2],
        after_sessions=[new_session_1],
        scope="week",
    )

    assert len(diff.removed) == 2
    assert len(diff.added) == 1
    assert diff.added[0].session_id == "session-3"
    assert len(diff.modified) == 0
    assert len(diff.unchanged) == 0


def test_scope_preserved(sample_session_1, modified_session_1):
    """Test that scope is correctly preserved in diff."""
    diff_day = build_plan_diff(
        before_sessions=[sample_session_1],
        after_sessions=[modified_session_1],
        scope="day",
    )
    assert diff_day.scope == "day"

    diff_week = build_plan_diff(
        before_sessions=[sample_session_1],
        after_sessions=[modified_session_1],
        scope="week",
    )
    assert diff_week.scope == "week"

    diff_plan = build_plan_diff(
        before_sessions=[sample_session_1],
        after_sessions=[modified_session_1],
        scope="plan",
    )
    assert diff_plan.scope == "plan"


def test_intent_change_detected(sample_session_1):
    """Test that intent changes are detected."""
    modified = PlannedSession(
        id="session-1",
        user_id="user-1",
        athlete_id=1,
        date=datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC),
        type="Run",
        title="Easy Run",
        distance_mi=5.0,
        duration_minutes=40,
        intent="quality",  # Changed from "easy"
        plan_type="race",
        source="planner_v2",
    )

    diff = build_plan_diff(
        before_sessions=[sample_session_1],
        after_sessions=[modified],
        scope="day",
    )

    assert len(diff.modified) == 1
    assert diff.modified[0].session_id == "session-1"
    assert len(diff.modified[0].changes) == 1
    assert diff.modified[0].changes[0].field == "intent"
    assert diff.modified[0].changes[0].before == "easy"
    assert diff.modified[0].changes[0].after == "quality"
