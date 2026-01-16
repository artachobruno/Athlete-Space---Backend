"""Tests for PlanRevision system.

Tests that revisions are created correctly for:
- modify_day
- modify_week
- blocked race week modifications
- partial outcomes when warnings present
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.coach.tools.modify_day import modify_day
from app.coach.tools.modify_week import modify_week
from app.db.models import AthleteProfile, PlannedSession
from app.plans.modify.types import DayModification
from app.plans.modify.week_types import WeekModification
from app.plans.race.constants import TAPER_WEEKS_DEFAULT
from app.plans.revision.builder import PlanRevisionBuilder
from app.plans.revision.types import PlanRevision, RevisionOutcome


@pytest.fixture
def athlete_profile() -> AthleteProfile:
    """Fixture for athlete profile with race date."""
    return AthleteProfile(
        user_id="test_user",
        athlete_id=1,
        race_date=date(2026, 6, 15),
        taper_weeks=TAPER_WEEKS_DEFAULT,
        sources={},
    )


@pytest.fixture
def planned_session() -> PlannedSession:
    """Fixture for a planned session."""
    return PlannedSession(
        id="session_1",
        user_id="test_user",
        athlete_id=1,
        date=datetime(2026, 6, 10, 8, 0, tzinfo=UTC),
        type="Run",
        title="Easy Run",
        plan_type="race",
        intent="easy",
        distance_mi=5.0,
        duration_minutes=None,
    )


def clone_planned_session(session: PlannedSession) -> PlannedSession:
    """Clone a PlannedSession, filtering out SQLAlchemy internal state.

    Args:
        session: Original PlannedSession to clone

    Returns:
        New PlannedSession instance with same data, without ORM state
    """
    data = {k: v for k, v in session.__dict__.items() if not k.startswith("_sa_")}
    return PlannedSession(**data)


def test_revision_builder_creates_revision() -> None:
    """Test that PlanRevisionBuilder creates a valid revision."""
    builder = PlanRevisionBuilder(scope="day", user_request="Reduce distance by 1 mile")
    builder.set_reason("Feeling tired")
    builder.set_range("2026-06-10", "2026-06-10")
    builder.add_delta(
        entity_type="session",
        entity_id="s1",
        date="2026-06-10",
        field="distance_mi",
        old=5.0,
        new=4.0,
    )
    builder.add_rule(
        rule_id="TEST_RULE",
        description="Test rule",
        severity="info",
        triggered=False,
    )

    revision = builder.finalize()

    assert revision.scope == "day"
    assert revision.user_request == "Reduce distance by 1 mile"
    assert revision.reason == "Feeling tired"
    assert len(revision.deltas) == 1
    assert len(revision.rules) == 1
    assert revision.outcome == "applied"
    assert revision.affected_range == {"start": "2026-06-10", "end": "2026-06-10"}


def test_revision_builder_blocked_outcome() -> None:
    """Test that blocked rule sets outcome to blocked."""
    builder = PlanRevisionBuilder(scope="week", user_request="Increase volume")
    builder.add_rule(
        rule_id="BLOCKING_RULE",
        description="This blocks the modification",
        severity="block",
        triggered=True,
    )

    revision = builder.finalize()

    assert revision.outcome == "blocked"
    assert len(revision.rules) == 1
    assert revision.rules[0].triggered is True


def test_revision_builder_partially_applied_outcome() -> None:
    """Test that warnings set outcome to partially_applied."""
    builder = PlanRevisionBuilder(scope="week", user_request="Modify week")
    builder.add_rule(
        rule_id="WARNING_RULE",
        description="This is a warning",
        severity="warning",
        triggered=True,
    )

    revision = builder.finalize()

    assert revision.outcome == "partially_applied"
    assert len(revision.rules) == 1


def test_revision_builder_applied_outcome() -> None:
    """Test that no warnings or blocks results in applied outcome."""
    builder = PlanRevisionBuilder(scope="day", user_request="Modify day")
    builder.add_rule(
        rule_id="INFO_RULE",
        description="This is just info",
        severity="info",
        triggered=False,
    )

    revision = builder.finalize()

    assert revision.outcome == "applied"


def test_modify_day_creates_revision(
    planned_session: PlannedSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that modify_day creates a revision with deltas."""
    # Mock the repository functions
    def mock_get_session(*args, **kwargs):
        return planned_session

    def mock_save_session(*args, **kwargs):
        saved = clone_planned_session(planned_session)
        saved.id = "session_2"
        saved.distance_mi = 4.0
        return saved

    monkeypatch.setattr(
        "app.coach.tools.modify_day.get_planned_session_by_date",
        mock_get_session,
    )
    monkeypatch.setattr(
        "app.coach.tools.modify_day.save_modified_session",
        mock_save_session,
    )

    modification = DayModification(
        change_type="adjust_distance",
        value=4.0,
        reason="Reduce distance",
    )

    result = modify_day(
        context={
            "user_id": "test_user",
            "athlete_id": 1,
            "target_date": "2026-06-10",
            "modification": modification.model_dump(),
            "user_request": "Reduce distance to 4 miles",
        },
        athlete_profile=None,  # No race day protection in test
    )

    assert result["success"] is True
    assert "revision" in result
    revision: PlanRevision = result["revision"]
    assert revision.scope == "day"
    assert revision.user_request == "Reduce distance to 4 miles"
    assert len(revision.deltas) > 0
    # Find distance delta
    distance_delta = next((d for d in revision.deltas if d.field == "distance_mi"), None)
    assert distance_delta is not None
    assert distance_delta.old == 5.0
    assert distance_delta.new == 4.0


def test_modify_week_creates_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that modify_week creates a revision."""
    # Create mock sessions
    start_date = date(2026, 6, 8)
    sessions = []
    for i in range(7):
        session_date = start_date + timedelta(days=i)
        session = PlannedSession(
            id=f"session_{i}",
            user_id="test_user",
            athlete_id=1,
            date=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
            type="Run",
            title=f"Session {i}",
            plan_type="race",
            intent="easy",
            distance_mi=5.0,
        )
        sessions.append(session)

    def mock_get_sessions(*args, **kwargs):
        return sessions

    def mock_save_sessions(*args, **kwargs):
        # Return modified sessions with reduced distance
        modified = []
        for s in sessions:
            new_s = clone_planned_session(s)
            new_s.id = f"modified_{s.id}"
            new_s.distance_mi = s.distance_mi * 0.8  # 20% reduction
            modified.append(new_s)
        return modified

    monkeypatch.setattr(
        "app.coach.tools.modify_week.get_planned_sessions_in_range",
        mock_get_sessions,
    )
    monkeypatch.setattr(
        "app.coach.tools.modify_week.save_modified_sessions",
        mock_save_sessions,
    )

    modification = WeekModification(
        change_type="reduce_volume",
        start_date="2026-06-08",
        end_date="2026-06-14",
        percent=0.2,
        reason="Reduce volume",
    )

    result = modify_week(
        user_id="test_user",
        athlete_id=1,
        modification=modification,
        user_request="Reduce volume by 20%",
        athlete_profile=None,  # No race/taper protection in test
    )

    assert result["success"] is True
    assert "revision" in result
    revision: PlanRevision = result["revision"]
    assert revision.scope == "week"
    assert revision.user_request == "Reduce volume by 20%"
    assert len(revision.deltas) > 0


def test_blocked_race_week_creates_blocked_revision(
    athlete_profile: AthleteProfile,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that blocked race week modification creates blocked revision."""
    race_date = athlete_profile.race_date
    week_start = race_date - timedelta(days=race_date.weekday())
    week_end = week_start + timedelta(days=6)

    sessions = []
    for i in range(7):
        session_date = week_start + timedelta(days=i)
        session = PlannedSession(
            id=f"session_{i}",
            user_id="test_user",
            athlete_id=1,
            date=datetime.combine(session_date, datetime.min.time()).replace(tzinfo=UTC),
            type="Run",
            title=f"Session {i}",
            plan_type="race",
            intent="easy",
            distance_mi=5.0,
        )
        sessions.append(session)

    def mock_get_sessions(*args, **kwargs):
        return sessions

    monkeypatch.setattr(
        "app.coach.tools.modify_week.get_planned_sessions_in_range",
        mock_get_sessions,
    )

    modification = WeekModification(
        change_type="increase_volume",
        start_date=week_start.isoformat(),
        end_date=week_end.isoformat(),
        percent=0.1,
    )

    result = modify_week(
        user_id="test_user",
        athlete_id=1,
        modification=modification,
        user_request="Increase volume",
        athlete_profile=athlete_profile,  # Pass explicitly for race week protection
    )

    assert result["success"] is False
    assert "revision" in result
    revision: PlanRevision = result["revision"]
    assert revision.outcome == "blocked"
    # Check that blocking rule is present
    blocking_rules = [r for r in revision.rules if r.severity == "block" and r.triggered]
    assert len(blocking_rules) > 0


def test_revision_serialization() -> None:
    """Test that revisions can be serialized to JSON."""
    builder = PlanRevisionBuilder(scope="day", user_request="Test")
    builder.add_delta(
        entity_type="session",
        entity_id="s1",
        field="distance_mi",
        old=5.0,
        new=4.0,
    )
    revision = builder.finalize()

    # Serialize
    from app.plans.revision.serializers import serialize_revision

    serialized = serialize_revision(revision)
    assert isinstance(serialized, dict)
    assert serialized["scope"] == "day"
    assert serialized["user_request"] == "Test"
    assert len(serialized["deltas"]) == 1

    # Deserialize
    from app.plans.revision.serializers import deserialize_revision

    deserialized = deserialize_revision(serialized)
    assert isinstance(deserialized, PlanRevision)
    assert deserialized.scope == "day"
    assert deserialized.user_request == "Test"


def test_explanation_payload_builder() -> None:
    """Test that explanation payload can be built from revision."""
    builder = PlanRevisionBuilder(scope="week", user_request="Reduce volume")
    builder.add_delta(
        entity_type="session",
        entity_id="s1",
        field="distance_mi",
        old=5.0,
        new=4.0,
    )
    revision = builder.finalize()

    from app.plans.revision.explanation_payload import build_explanation_payload

    athlete_context = {"athlete_id": 1, "name": "Test Athlete"}

    payload = build_explanation_payload(
        revision=revision,
        athlete_context=athlete_context,
    )

    assert "revision" in payload
    assert "athlete" in payload
    assert "instructions" in payload
    assert payload["athlete"] == athlete_context
    assert payload["instructions"]["style"] == "coach"
