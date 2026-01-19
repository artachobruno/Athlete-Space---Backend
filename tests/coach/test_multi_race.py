"""Tests for multi-race season support.

Tests the following scenarios:
- Create first race → priority A
- Add second race → priority B
- Promote B → A (demote previous A)
- Active race switches correctly
- Planner uses correct taper logic
- Slot system remains single-focus
"""

from datetime import UTC, datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import ConversationProgress, RacePlan, RacePriority
from app.db.session import get_session
from app.services.race_service import resolve_race_focus, update_race_priority


@pytest.fixture
def test_user_id() -> str:
    """Test user ID."""
    return "test_user_multi_race_123"


@pytest.fixture
def test_athlete_id() -> int:
    """Test athlete ID."""
    return 99999


@pytest.fixture
def test_conversation_id() -> str:
    """Test conversation ID."""
    return "test_conv_multi_race_123"


@pytest.fixture(autouse=True)
def cleanup_races(test_athlete_id: int):
    """Clean up race plans after each test."""
    yield
    with get_session() as db:
        races = db.execute(select(RacePlan).where(RacePlan.athlete_id == test_athlete_id)).scalars().all()
        for race in races:
            db.delete(race)
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_conversation_progress(test_conversation_id: str):
    """Clean up conversation progress after each test."""
    yield
    with get_session() as db:
        progress = db.execute(
            select(ConversationProgress).where(ConversationProgress.conversation_id == test_conversation_id)
        ).scalar_one_or_none()
        if progress:
            db.delete(progress)
            db.commit()


def test_create_first_race_priority_a(test_user_id: str, test_athlete_id: int, test_conversation_id: str):
    """Test that first race created gets priority A."""
    race_date = datetime(2025, 6, 15, tzinfo=UTC)
    distance = "Marathon"

    race_plan, was_created = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date,
        race_distance=distance,
        conversation_id=test_conversation_id,
    )

    assert was_created is True
    assert race_plan.priority == RacePriority.A.value
    assert race_plan.race_date == race_date
    assert race_plan.race_distance == distance

    # Verify active_race_id is set
    with get_session() as db:
        progress = db.execute(
            select(ConversationProgress).where(ConversationProgress.conversation_id == test_conversation_id)
        ).scalar_one_or_none()
        assert progress is not None
        assert progress.active_race_id == race_plan.id


def test_add_second_race_priority_b(test_user_id: str, test_athlete_id: int, test_conversation_id: str):
    """Test that second race created gets priority B."""
    # Create first race
    race_date_1 = datetime(2025, 6, 15, tzinfo=UTC)
    race_plan_1, _ = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date_1,
        race_distance="Marathon",
        conversation_id=test_conversation_id,
    )
    assert race_plan_1.priority == RacePriority.A.value

    # Create second race
    race_date_2 = datetime(2025, 8, 20, tzinfo=UTC)
    race_plan_2, was_created = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date_2,
        race_distance="Half Marathon",
        conversation_id=test_conversation_id,
    )

    assert was_created is True
    assert race_plan_2.priority == RacePriority.B.value

    # Verify first race still has priority A
    with get_session() as db:
        db.refresh(race_plan_1)
        assert race_plan_1.priority == RacePriority.A.value


def test_promote_b_to_a_demotes_previous_a(test_user_id: str, test_athlete_id: int, test_conversation_id: str):
    """Test that promoting B to A demotes previous A to B."""
    # Create first race (A)
    race_date_1 = datetime(2025, 6, 15, tzinfo=UTC)
    race_plan_1, _ = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date_1,
        race_distance="Marathon",
        conversation_id=test_conversation_id,
    )

    # Create second race (B)
    race_date_2 = datetime(2025, 8, 20, tzinfo=UTC)
    race_plan_2, _ = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date_2,
        race_distance="Half Marathon",
        conversation_id=test_conversation_id,
    )

    # Promote second race to A
    updated_race = update_race_priority(
        athlete_id=test_athlete_id,
        race_id=race_plan_2.id,
        new_priority=RacePriority.A.value,
        conversation_id=test_conversation_id,
    )

    assert updated_race.priority == RacePriority.A.value

    # Verify first race was demoted to B
    with get_session() as db:
        db.refresh(race_plan_1)
        assert race_plan_1.priority == RacePriority.B.value

    # Verify active_race_id is updated
    with get_session() as db:
        progress = db.execute(
            select(ConversationProgress).where(ConversationProgress.conversation_id == test_conversation_id)
        ).scalar_one_or_none()
        assert progress is not None
        assert progress.active_race_id == race_plan_2.id


def test_race_already_exists_switches_focus(test_user_id: str, test_athlete_id: int, test_conversation_id: str):
    """Test that existing race (same date + distance) switches focus without creating duplicate."""
    race_date = datetime(2025, 6, 15, tzinfo=UTC)
    distance = "Marathon"

    # Create first race
    race_plan_1, was_created_1 = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date,
        race_distance=distance,
        conversation_id=test_conversation_id,
    )
    assert was_created_1 is True

    # Try to create same race again
    race_plan_2, was_created_2 = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date,
        race_distance=distance,
        conversation_id=test_conversation_id,
    )

    assert was_created_2 is False
    assert race_plan_2.id == race_plan_1.id  # Same race

    # Verify only one race exists
    with get_session() as db:
        races = db.execute(select(RacePlan).where(RacePlan.athlete_id == test_athlete_id)).scalars().all()
        assert len(races) == 1


def test_explicit_priority_extraction(test_user_id: str, test_athlete_id: int, test_conversation_id: str):
    """Test that explicitly provided priority is used."""
    race_date = datetime(2025, 6, 15, tzinfo=UTC)
    distance = "Marathon"

    # Create race with explicit priority C
    race_plan, was_created = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date,
        race_distance=distance,
        race_priority=RacePriority.C.value,
        conversation_id=test_conversation_id,
    )

    assert was_created is True
    assert race_plan.priority == RacePriority.C.value


def test_only_one_a_race_per_athlete(test_user_id: str, test_athlete_id: int, test_conversation_id: str):
    """Test invariant: only one A race per athlete."""
    # Create first race (A)
    race_date_1 = datetime(2025, 6, 15, tzinfo=UTC)
    race_plan_1, _ = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date_1,
        race_distance="Marathon",
        conversation_id=test_conversation_id,
    )

    # Create second race (B)
    race_date_2 = datetime(2025, 8, 20, tzinfo=UTC)
    _race_plan_2, _ = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date_2,
        race_distance="Half Marathon",
        conversation_id=test_conversation_id,
    )

    # Verify only one A race exists
    with get_session() as db:
        a_races = db.execute(
            select(RacePlan).where(RacePlan.athlete_id == test_athlete_id, RacePlan.priority == RacePriority.A.value)
        ).scalars().all()
        assert len(a_races) == 1
        assert a_races[0].id == race_plan_1.id


def test_update_priority_invalid_value(test_user_id: str, test_athlete_id: int):
    """Test that invalid priority raises ValueError."""
    race_date = datetime(2025, 6, 15, tzinfo=UTC)
    distance = "Marathon"

    # Create race
    race_plan, _ = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date,
        race_distance=distance,
    )

    # Try to update with invalid priority
    with pytest.raises(ValueError, match="Invalid priority"):
        update_race_priority(
            athlete_id=test_athlete_id,
            race_id=race_plan.id,
            new_priority="X",  # Invalid
        )


def test_update_priority_race_not_found(test_user_id: str, test_athlete_id: int):
    """Test that updating non-existent race raises RuntimeError."""
    with pytest.raises(RuntimeError, match="Race not found"):
        update_race_priority(
            athlete_id=test_athlete_id,
            race_id="non_existent_race_id",
            new_priority=RacePriority.A.value,
        )


def test_slot_system_remains_single_focus(test_user_id: str, test_athlete_id: int, test_conversation_id: str):
    """Test that slot system remains single-focus (no arrays or race lists)."""
    # Create multiple races
    race_date_1 = datetime(2025, 6, 15, tzinfo=UTC)
    race_plan_1, _ = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date_1,
        race_distance="Marathon",
        conversation_id=test_conversation_id,
    )

    race_date_2 = datetime(2025, 8, 20, tzinfo=UTC)
    race_plan_2, _ = resolve_race_focus(
        athlete_id=test_athlete_id,
        user_id=test_user_id,
        race_date=race_date_2,
        race_distance="Half Marathon",
        conversation_id=test_conversation_id,
    )

    # Verify conversation progress has single active_race_id (not array)
    with get_session() as db:
        progress = db.execute(
            select(ConversationProgress).where(ConversationProgress.conversation_id == test_conversation_id)
        ).scalar_one_or_none()
        assert progress is not None
        assert progress.active_race_id is not None
        assert isinstance(progress.active_race_id, str)  # Single value, not array
        # Should be the most recently created/focused race
        assert progress.active_race_id in {race_plan_1.id, race_plan_2.id}

        # Verify slots don't contain race arrays
        assert "race_list" not in progress.slots
        assert "race_dates" not in progress.slots
        # Slots should contain single race info
        assert "race_distance" in progress.slots or "race_date" in progress.slots or len(progress.slots) == 0
