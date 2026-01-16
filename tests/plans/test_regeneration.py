"""Tests for plan regeneration.

Tests that regeneration:
- Does not touch past sessions
- Replaces future sessions
- Creates a revision
- Is blocked in race week (unless allow_race_week)
- Is idempotent (same input → same output)
- Regenerated sessions reference revision_id
"""

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.db.models import AthleteProfile, PlannedSession
from app.db.session import get_session
from app.plans.modify.plan_revision_repo import list_regenerations
from app.plans.regenerate.regeneration_service import regenerate_plan
from app.plans.regenerate.types import RegenerationRequest


@pytest.fixture
def athlete_profile() -> AthleteProfile:
    """Fixture for athlete profile with race date."""
    return AthleteProfile(
        user_id="test_user",
        athlete_id=1,
        race_date=date(2026, 6, 15),
        taper_weeks=2,
    )


@pytest.fixture
def past_session() -> PlannedSession:
    """Fixture for a past planned session."""
    return PlannedSession(
        id="past_session_1",
        user_id="test_user",
        athlete_id=1,
        date=datetime.now(UTC) - timedelta(days=5),
        type="Run",
        title="Past Easy Run",
        plan_type="race",
        plan_id="race_marathon_20260615",
        intent="easy",
        distance_mi=5.0,
        status="planned",
    )


@pytest.fixture
def future_sessions() -> list[PlannedSession]:
    """Fixture for future planned sessions."""
    today = datetime.now(UTC).date()
    return [
        PlannedSession(
            id=f"future_session_{i}",
            user_id="test_user",
            athlete_id=1,
            date=datetime.combine(today + timedelta(days=i), datetime.min.time()).replace(tzinfo=UTC),
            type="Run",
            title=f"Future Run {i}",
            plan_type="race",
            plan_id="race_marathon_20260615",
            intent="easy",
            distance_mi=5.0 + i,
            status="planned",
        )
        for i in range(1, 8)  # 7 future sessions
    ]


def test_regeneration_does_not_touch_past_sessions(
    athlete_profile: AthleteProfile,
    past_session: PlannedSession,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration does not modify past sessions."""
    with get_session() as session:
        # Setup: Create past and future sessions
        session.add(athlete_profile)
        session.add(past_session)
        for s in future_sessions:
            session.add(s)
        session.commit()

        # Get original past session state
        original_past_status = past_session.status
        original_past_notes = past_session.notes

        # Regenerate from today
        today = datetime.now(UTC).date()
        req = RegenerationRequest(
            start_date=today,
            mode="partial",
            reason="Test regeneration",
        )

        # Execute regeneration (sync function)
        regenerate_plan(
            user_id="test_user",
            athlete_id=1,
            req=req,
        )

        # Refresh past session
        session.refresh(past_session)

        # Assert past session unchanged
        assert past_session.status == original_past_status
        assert past_session.notes == original_past_notes


def test_regeneration_replaces_future_sessions(
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration replaces future sessions."""
    with get_session() as session:
        # Setup: Create future sessions
        session.add(athlete_profile)
        for s in future_sessions:
            session.add(s)
        session.commit()

        # Regenerate from today
        today = datetime.now(UTC).date()
        req = RegenerationRequest(
            start_date=today,
            mode="partial",
            reason="Test regeneration",
        )

        # Execute regeneration
        revision = regenerate_plan(
            user_id="test_user",
            athlete_id=1,
            req=req,
        )

        # Check that old sessions are marked as cancelled
        for old_session in future_sessions:
            session.refresh(old_session)
            assert old_session.status == "cancelled"
            assert revision.id in (old_session.notes or "")

        # Check that new sessions exist with revision_id
        new_sessions = session.query(PlannedSession).filter(
            PlannedSession.revision_id == revision.id,
        ).all()

        assert len(new_sessions) > 0
        for new_session in new_sessions:
            assert new_session.revision_id == revision.id
            assert new_session.status == "planned"


def test_regeneration_creates_revision(
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration creates a PlanRevision."""
    with get_session() as session:
        # Setup
        session.add(athlete_profile)
        for s in future_sessions:
            session.add(s)
        session.commit()

        # Regenerate
        today = datetime.now(UTC).date()
        req = RegenerationRequest(
            start_date=today,
            mode="partial",
            reason="Test regeneration",
        )

        revision = regenerate_plan(
            user_id="test_user",
            athlete_id=1,
            req=req,
        )

        # Check revision exists
        assert revision is not None
        assert revision.revision_type == "regenerate_plan"
        assert revision.status == "regenerated"
        assert revision.affected_start == today
        assert revision.reason == "Test regeneration"
        assert revision.deltas is not None
        assert revision.deltas.get("regeneration_mode") == "partial"


def test_regeneration_blocked_in_race_week(
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration is blocked in race week unless allow_race_week."""
    with get_session() as session:
        # Setup: Create sessions in race week
        race_date = athlete_profile.race_date
        assert race_date is not None

        # Calculate race week start (Monday of race week)
        days_since_monday = race_date.weekday()
        race_week_start = race_date - timedelta(days=days_since_monday)

        # Create session in race week
        race_week_session = PlannedSession(
            id="race_week_session",
            user_id="test_user",
            athlete_id=1,
            date=datetime.combine(race_week_start, datetime.min.time()).replace(tzinfo=UTC),
            type="Run",
            title="Race Week Run",
            plan_type="race",
            plan_id="race_marathon_20260615",
            intent="easy",
            distance_mi=3.0,
            status="planned",
        )

        session.add(athlete_profile)
        session.add(race_week_session)
        session.commit()

        # Try to regenerate in race week (should fail)
        req = RegenerationRequest(
            start_date=race_week_start,
            mode="partial",
            reason="Test regeneration",
            allow_race_week=False,
        )

        with pytest.raises(ValueError, match="race week"):
            regenerate_plan(
                user_id="test_user",
                athlete_id=1,
                req=req,
            )

        # Try with allow_race_week=True (should succeed)
        req.allow_race_week = True
        revision = regenerate_plan(
            user_id="test_user",
            athlete_id=1,
            req=req,
        )

        assert revision is not None
        assert revision.status == "regenerated"


def test_regeneration_idempotent(
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration is idempotent (same input → same output)."""
    with get_session() as session:
        # Setup
        session.add(athlete_profile)
        for s in future_sessions:
            session.add(s)
        session.commit()

        today = datetime.now(UTC).date()
        req = RegenerationRequest(
            start_date=today,
            mode="partial",
            reason="Test regeneration",
        )

        # First regeneration
        revision1 = regenerate_plan(
            user_id="test_user",
            athlete_id=1,
            req=req,
        )

        # Get new sessions from first regeneration
        new_sessions_1 = session.query(PlannedSession).filter(
            PlannedSession.revision_id == revision1.id,
        ).all()

        # Second regeneration with same input
        revision2 = regenerate_plan(
            user_id="test_user",
            athlete_id=1,
            req=req,
        )

        # Get new sessions from second regeneration
        new_sessions_2 = session.query(PlannedSession).filter(
            PlannedSession.revision_id == revision2.id,
        ).all()

        # Both should succeed and create revisions
        assert revision1 is not None
        assert revision2 is not None
        assert revision1.id != revision2.id  # Different revision IDs

        # Both should have same number of sessions (idempotent output)
        assert len(new_sessions_1) == len(new_sessions_2)


def test_regenerated_sessions_reference_revision_id(
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regenerated sessions reference revision_id."""
    with get_session() as session:
        # Setup
        session.add(athlete_profile)
        for s in future_sessions:
            session.add(s)
        session.commit()

        # Regenerate
        today = datetime.now(UTC).date()
        req = RegenerationRequest(
            start_date=today,
            mode="partial",
            reason="Test regeneration",
        )

        revision = regenerate_plan(
            user_id="test_user",
            athlete_id=1,
            req=req,
        )

        # Check all new sessions have revision_id
        new_sessions = session.query(PlannedSession).filter(
            PlannedSession.revision_id == revision.id,
        ).all()

        assert len(new_sessions) > 0
        for new_session in new_sessions:
            assert new_session.revision_id == revision.id
            assert new_session.status == "planned"


def test_list_regenerations(
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that list_regenerations returns only regeneration revisions."""
    with get_session() as session:
        # Setup
        session.add(athlete_profile)
        for s in future_sessions:
            session.add(s)
        session.commit()

        # Create multiple regenerations
        today = datetime.now(UTC).date()
        for i in range(3):
            req = RegenerationRequest(
                start_date=today,
                mode="partial",
                reason=f"Test regeneration {i}",
            )
            regenerate_plan(
                user_id="test_user",
                athlete_id=1,
                req=req,
            )

        # List regenerations
        regenerations = list_regenerations(session, athlete_id=1)

        # Should have 3 regenerations
        assert len(regenerations) == 3
        for reg in regenerations:
            assert reg.revision_type == "regenerate_plan"
            assert reg.athlete_id == 1
