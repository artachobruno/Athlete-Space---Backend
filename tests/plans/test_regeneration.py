"""Tests for plan regeneration.

Tests that regeneration:
- Does not touch past sessions
- Replaces future sessions
- Creates a revision
- Is blocked in race week (unless allow_race_week)
- Is idempotent (same input → same output)
- Regenerated sessions reference revision_id
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

# CRITICAL: Import Workout model to register it in Base.metadata
# This must happen before PlannedSession tries to resolve ForeignKey("workouts.id")
import app.plans.regenerate.regeneration_executor as rex  # Module that calls plan_race
import app.workouts.models  # Registers Workout in Base.metadata
from app.db.models import AthleteProfile, PlannedSession
from app.plans.modify.plan_revision_repo import list_regenerations
from app.plans.regenerate.regeneration_service import regenerate_plan
from app.plans.regenerate.types import RegenerationRequest


@pytest.fixture(autouse=True)
def mock_plan_race(monkeypatch):
    """Mock plan_race to return deterministic test data without running real planner.

    This fixture mocks the planner call so tests can validate regeneration semantics
    (past sessions unchanged, future sessions cancelled, revision created, etc.)
    without the slow planner execution or network dependencies.
    """
    async def _fake_plan_race(
        race_date: datetime,
        distance: str,
        user_id: str,
        athlete_id: int,
        *,
        start_date: datetime | None = None,
        athlete_state=None,
        progress_callback=None,
    ) -> tuple[list[dict], int]:
        """Fake plan_race that returns deterministic test sessions.

        Returns sessions starting from start_date (or today if None) for 7 days.
        Each session has minimal required fields for PlannedSession creation.
        """
        # Trivial await to satisfy linter (mock function must be async to match real function)
        await asyncio.sleep(0)

        # Use start_date if provided, otherwise use today
        if start_date:
            base_date = start_date.date()
        else:
            base_date = datetime.now(UTC).date()

        # Generate 7 sessions (one per day for a week)
        sessions = []
        for i in range(7):
            session_date = datetime.combine(
                base_date + timedelta(days=i),
                datetime.min.time(),
            ).replace(tzinfo=UTC)

            sessions.append({
                "date": session_date,
                "type": "Run",
                "title": f"Stub Run {i + 1}",
                "intent": "easy" if i % 2 == 0 else "tempo",
                "distance_mi": 3.0 + (i * 0.5),
                "status": "planned",
                "plan_type": "race",
                "plan_id": "race_marathon_20260615",
            })

        return (sessions, 1)  # Return tuple: (list of session dicts, total weeks)

    # Patch plan_race where it's imported/used in regeneration_executor
    monkeypatch.setattr(rex, "plan_race", _fake_plan_race)


@pytest.fixture
def athlete_profile() -> AthleteProfile:
    """Fixture for athlete profile with race date.

    Uses unique user_id per test to avoid UniqueViolation errors.
    """
    user_id = f"test_user_{uuid.uuid4().hex[:8]}"
    return AthleteProfile(
        user_id=user_id,
        athlete_id=1,
        race_date=datetime(2026, 6, 15, tzinfo=UTC),  # Use datetime to match DB column type
        taper_weeks=2,
        sources={},
    )


@pytest.fixture
def past_session(athlete_profile: AthleteProfile) -> PlannedSession:
    """Fixture for a past planned session."""
    return PlannedSession(
        id="past_session_1",
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
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
def future_sessions(athlete_profile: AthleteProfile) -> list[PlannedSession]:
    """Fixture for future planned sessions."""
    today = datetime.now(UTC).date()
    return [
        PlannedSession(
            id=f"future_session_{i}",
            user_id=athlete_profile.user_id,
            athlete_id=athlete_profile.athlete_id,
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
    db_session: Session,
    athlete_profile: AthleteProfile,
    past_session: PlannedSession,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration does not modify past sessions."""
    session = db_session
    # Setup: Create past and future sessions
    session.add(athlete_profile)
    session.add(past_session)
    for s in future_sessions:
        session.add(s)
    session.commit()

    # Get original past session state
    original_past_status = past_session.status
    original_past_notes = past_session.notes

    # Regenerate from tomorrow (future_sessions start at today+1)
    today = datetime.now(UTC).date()
    start_date = today + timedelta(days=1)
    req = RegenerationRequest(
        start_date=start_date,
        mode="partial",
        reason="Test regeneration",
    )

    # Execute regeneration (sync function)
    regenerate_plan(
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
        req=req,
    )

    # Refresh past session
    session.refresh(past_session)

    # Assert past session unchanged
    assert past_session.status == original_past_status
    assert past_session.notes == original_past_notes


def test_regeneration_replaces_future_sessions(
    db_session: Session,
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration replaces future sessions."""
    session = db_session
    # Setup: Create future sessions
    session.add(athlete_profile)
    for s in future_sessions:
        session.add(s)
    session.commit()

    # Regenerate from tomorrow (future_sessions start at today+1)
    today = datetime.now(UTC).date()
    start_date = today + timedelta(days=1)
    req = RegenerationRequest(
        start_date=start_date,
        mode="partial",
        reason="Test regeneration",
    )

    # Execute regeneration
    revision = regenerate_plan(
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
        req=req,
    )

    # Check that old sessions are marked as cancelled
    for old_session in future_sessions:
        session.refresh(old_session)
        assert old_session.status == "cancelled"
        assert revision.id in (old_session.notes or "")

    # Check that new sessions exist with revision_id
    new_sessions = list(
        session.execute(
            select(PlannedSession).where(PlannedSession.revision_id == revision.id)
        ).scalars().all()
    )

    assert len(new_sessions) > 0
    for new_session in new_sessions:
        assert new_session.revision_id == revision.id
        assert new_session.status == "planned"


def test_regeneration_creates_revision(
    db_session: Session,
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration creates a PlanRevision."""
    session = db_session
    # Setup
    session.add(athlete_profile)
    for s in future_sessions:
        session.add(s)
    session.commit()

    # Regenerate from tomorrow (future_sessions start at today+1)
    today = datetime.now(UTC).date()
    start_date = today + timedelta(days=1)
    req = RegenerationRequest(
        start_date=start_date,
        mode="partial",
        reason="Test regeneration",
    )

    revision = regenerate_plan(
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
        req=req,
    )

    # Check revision exists
    assert revision is not None
    assert revision.revision_type == "regenerate_plan"
    assert revision.status == "regenerated"
    assert revision.affected_start == start_date
    assert revision.reason == "Test regeneration"
    assert revision.deltas is not None
    assert revision.deltas.get("regeneration_mode") == "partial"


def test_regeneration_blocked_in_race_week(
    db_session: Session,
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration is blocked in race week unless allow_race_week."""
    session = db_session
    # Setup: Create sessions in race week
    race_date = athlete_profile.race_date
    assert race_date is not None

    # Normalize race_date to date for calculations
    race_date_normalized = race_date.date() if hasattr(race_date, "date") else race_date

    # Calculate race week start (Monday of race week)
    days_since_monday = race_date_normalized.weekday()
    race_week_start = race_date_normalized - timedelta(days=days_since_monday)

    # Create session in race week
    race_week_session = PlannedSession(
        id="race_week_session",
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
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
            user_id=athlete_profile.user_id,
            athlete_id=athlete_profile.athlete_id,
            req=req,
        )

    # Try with allow_race_week=True (should succeed)
    req.allow_race_week = True
    revision = regenerate_plan(
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
        req=req,
    )

    assert revision is not None
    assert revision.status == "regenerated"


def test_regeneration_idempotent(
    db_session: Session,
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regeneration is idempotent (same input → same output)."""
    session = db_session
    # Setup
    session.add(athlete_profile)
    for s in future_sessions:
        session.add(s)
    session.commit()

    # Regenerate from tomorrow (future_sessions start at today+1)
    today = datetime.now(UTC).date()
    start_date = today + timedelta(days=1)
    req = RegenerationRequest(
        start_date=start_date,
        mode="partial",
        reason="Test regeneration",
    )

    # First regeneration
    revision1 = regenerate_plan(
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
        req=req,
    )

    # Get new sessions from first regeneration
    new_sessions_1 = list(
        session.execute(
            select(PlannedSession).where(PlannedSession.revision_id == revision1.id)
        ).scalars().all()
    )

    # Second regeneration with same input
    revision2 = regenerate_plan(
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
        req=req,
    )

    # Get new sessions from second regeneration
    new_sessions_2 = list(
        session.execute(
            select(PlannedSession).where(PlannedSession.revision_id == revision2.id)
        ).scalars().all()
    )

    # Both should succeed and create revisions
    assert revision1 is not None
    assert revision2 is not None
    assert revision1.id != revision2.id  # Different revision IDs

    # Both should have same number of sessions (idempotent output)
    assert len(new_sessions_1) == len(new_sessions_2)


def test_regenerated_sessions_reference_revision_id(
    db_session: Session,
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that regenerated sessions reference revision_id."""
    session = db_session
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
        user_id=athlete_profile.user_id,
        athlete_id=athlete_profile.athlete_id,
        req=req,
    )

    # Check all new sessions have revision_id
    new_sessions = list(
        session.execute(
            select(PlannedSession).where(PlannedSession.revision_id == revision.id)
        ).scalars().all()
    )

    assert len(new_sessions) > 0
    for new_session in new_sessions:
        assert new_session.revision_id == revision.id
        assert new_session.status == "planned"


def test_list_regenerations(
    db_session: Session,
    athlete_profile: AthleteProfile,
    future_sessions: list[PlannedSession],
):
    """Test that list_regenerations returns only regeneration revisions."""
    session = db_session
    # Setup
    session.add(athlete_profile)
    for s in future_sessions:
        session.add(s)
    session.commit()

    # Create multiple regenerations from tomorrow (future_sessions start at today+1)
    today = datetime.now(UTC).date()
    start_date = today + timedelta(days=1)
    for i in range(3):
        req = RegenerationRequest(
            start_date=start_date,
            mode="partial",
            reason=f"Test regeneration {i}",
        )
        regenerate_plan(
            user_id=athlete_profile.user_id,
            athlete_id=athlete_profile.athlete_id,
            req=req,
        )

    # List regenerations
    regenerations = list_regenerations(session, athlete_id=athlete_profile.athlete_id)

    # Should have 3 regenerations
    assert len(regenerations) == 3
    for reg in regenerations:
        assert reg.revision_type == "regenerate_plan"
        assert reg.athlete_id == 1
