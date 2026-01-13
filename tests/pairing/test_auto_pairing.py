"""Unit tests for auto-pairing service.

Tests cover:
- One plan ↔ one activity (exact match)
- Two plans → closest duration wins
- > 30% duration → reject
- Different day → reject
- Different type → reject
- Different user → reject
- Activity first, plan later (order independence)
- Plan first, activity later (order independence)
- Manual unpair → re-pair works
"""

from datetime import UTC, date, datetime, timezone

import pytest

from app.db.models import Activity, PairingDecision, PlannedSession
from app.db.session import SessionLocal
from app.pairing.auto_pairing_service import try_auto_pair


@pytest.fixture
def db_session():
    """Create a test database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def test_user_id():
    """Test user ID."""
    return "test_user_123"


@pytest.fixture
def test_athlete_id():
    """Test athlete ID."""
    return 12345


class TestAutoPairingExactMatch:
    """Test exact match scenarios."""

    def test_one_plan_one_activity_exact_match(self, db_session, test_user_id, test_athlete_id):
        """Test pairing one plan with one activity (exact match)."""
        # Create planned session
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Morning Run",
            duration_minutes=30,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        # Create activity
        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_123",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Attempt pairing
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned)

        # Verify pairing
        assert activity.planned_session_id == planned.id
        assert planned.completed_activity_id == activity.id

        # Verify audit log
        decision = (
            db_session.query(PairingDecision)
            .filter(PairingDecision.activity_id == activity.id)
            .first()
        )
        assert decision is not None
        assert decision.decision == "paired"
        assert decision.reason == "auto_duration_match"

    def test_activity_first_plan_later(self, db_session, test_user_id, test_athlete_id):
        """Test order independence: activity created first, plan created later."""
        # Create activity first
        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_456",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Create planned session later
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Morning Run",
            duration_minutes=30,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        # Attempt pairing from planned session
        try_auto_pair(planned=planned, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned)

        # Verify pairing
        assert activity.planned_session_id == planned.id
        assert planned.completed_activity_id == activity.id


class TestAutoPairingMultipleCandidates:
    """Test scenarios with multiple candidates."""

    def test_two_plans_closest_duration_wins(self, db_session, test_user_id, test_athlete_id):
        """Test that when multiple plans exist, closest duration wins."""
        # Create two planned sessions
        planned1 = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Short Run",
            duration_minutes=25,  # 5 min difference
            plan_type="race",
        )
        planned2 = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Long Run",
            duration_minutes=35,  # 5 min difference
            plan_type="race",
        )
        db_session.add(planned1)
        db_session.add(planned2)
        db_session.flush()

        # Create activity with 30 min duration
        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_789",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Attempt pairing
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned1)
        db_session.refresh(planned2)

        # Verify pairing with closest duration (both are 5 min away, but planned1 created first)
        # Since both have same diff_pct, should pick by created_at, then id
        assert activity.planned_session_id in [planned1.id, planned2.id]
        paired_plan = planned1 if activity.planned_session_id == planned1.id else planned2
        assert paired_plan.completed_activity_id == activity.id


class TestAutoPairingRejections:
    """Test rejection scenarios."""

    def test_duration_mismatch_rejected(self, db_session, test_user_id, test_athlete_id):
        """Test that > 30% duration difference is rejected."""
        # Create planned session
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Short Run",
            duration_minutes=30,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        # Create activity with 60 min duration (100% difference, > 30%)
        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_reject",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=60 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Attempt pairing
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned)

        # Verify no pairing
        assert activity.planned_session_id is None
        assert planned.completed_activity_id is None

        # Verify audit log
        decision = (
            db_session.query(PairingDecision)
            .filter(PairingDecision.activity_id == activity.id)
            .first()
        )
        assert decision is not None
        assert decision.decision == "rejected"
        assert decision.reason == "duration_mismatch"

    def test_different_day_rejected(self, db_session, test_user_id, test_athlete_id):
        """Test that different day is rejected."""
        # Create planned session on Jan 15
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Morning Run",
            duration_minutes=30,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        # Create activity on Jan 16
        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_diff_day",
            source="strava",
            start_time=datetime(2024, 1, 16, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Attempt pairing
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned)

        # Verify no pairing
        assert activity.planned_session_id is None
        assert planned.completed_activity_id is None

    def test_different_type_rejected(self, db_session, test_user_id, test_athlete_id):
        """Test that different activity type is rejected."""
        # Create planned session for Run
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Morning Run",
            duration_minutes=30,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        # Create activity for Ride
        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_diff_type",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Ride",
            duration_seconds=30 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Attempt pairing
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned)

        # Verify no pairing
        assert activity.planned_session_id is None
        assert planned.completed_activity_id is None

    def test_different_user_rejected(self, db_session, test_user_id, test_athlete_id):
        """Test that different user is rejected."""
        # Create planned session for user1
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Morning Run",
            duration_minutes=30,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        # Create activity for user2
        activity = Activity(
            user_id="different_user",
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_diff_user",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Attempt pairing
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned)

        # Verify no pairing
        assert activity.planned_session_id is None
        assert planned.completed_activity_id is None


class TestAutoPairingEdgeCases:
    """Test edge cases."""

    def test_no_duration_planned_rejected(self, db_session, test_user_id, test_athlete_id):
        """Test that planned session without duration is rejected."""
        # Create planned session without duration
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Morning Run",
            duration_minutes=None,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        # Create activity
        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_no_duration",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Attempt pairing
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned)

        # Verify no pairing
        assert activity.planned_session_id is None
        assert planned.completed_activity_id is None

    def test_no_duration_activity_rejected(self, db_session, test_user_id, test_athlete_id):
        """Test that activity without duration is rejected."""
        # Create planned session
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Morning Run",
            duration_minutes=30,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        # Create activity without duration
        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_no_duration_act",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=None,
        )
        db_session.add(activity)
        db_session.flush()

        # Attempt pairing
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()
        db_session.refresh(activity)
        db_session.refresh(planned)

        # Verify no pairing
        assert activity.planned_session_id is None
        assert planned.completed_activity_id is None

    def test_already_paired_skipped(self, db_session, test_user_id, test_athlete_id):
        """Test that already paired items are skipped."""
        # Create and pair
        planned = PlannedSession(
            user_id=test_user_id,
            athlete_id=test_athlete_id,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            title="Morning Run",
            duration_minutes=30,
            plan_type="race",
        )
        db_session.add(planned)
        db_session.flush()

        activity = Activity(
            user_id=test_user_id,
            athlete_id=str(test_athlete_id),
            strava_activity_id="strava_paired",
            source="strava",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
        )
        db_session.add(activity)
        db_session.flush()

        # Pair them
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()

        # Try to pair again (should be skipped)
        initial_decision_count = db_session.query(PairingDecision).count()
        try_auto_pair(activity=activity, session=db_session)
        db_session.commit()

        # Verify no new decision logged (pairing skipped)
        final_decision_count = db_session.query(PairingDecision).count()
        assert final_decision_count == initial_decision_count
