"""Unit tests for calendar reconciliation engine.

Tests cover:
- Exact match
- Partial completion
- Wrong type substitution
- Multiple activities same day
- No activity
- Rest day behavior
- One-activity-per-session rule
"""

from datetime import UTC, date, datetime, timezone

import pytest

from app.calendar.reconciliation import (
    CompletedActivityInput,
    PlannedSessionInput,
    ReasonCode,
    ReconciliationConfig,
    SessionStatus,
    reconcile_sessions,
)


class TestReconciliationExactMatch:
    """Test exact match scenarios."""

    def test_exact_type_and_duration_match(self):
        """Test perfect match: same type, same duration."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.COMPLETED
        assert result.matched_activity_id == "activity-1"
        assert result.confidence == 1.0
        assert result.reason_code == ReasonCode.EXACT_MATCH
        assert "completed as planned" in result.explanation.lower()

    def test_exact_type_and_distance_match(self):
        """Test perfect match: same type, same distance."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=None,
            distance_km=5.0,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=None,
            distance_meters=5000.0,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.COMPLETED
        assert result.confidence == 1.0
        assert result.reason_code == ReasonCode.EXACT_MATCH

    def test_exact_match_with_both_duration_and_distance(self):
        """Test perfect match with both duration and distance."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=5.0,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
            distance_meters=5000.0,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.COMPLETED
        assert result.confidence == 1.0


class TestReconciliationPartial:
    """Test partial completion scenarios."""

    def test_duration_shortfall(self):
        """Test when duration is below threshold."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=60,
            distance_km=None,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,  # 30 minutes, below 80% of 60
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.PARTIAL
        assert result.matched_activity_id == "activity-1"
        assert result.confidence >= 0.6
        assert result.reason_code == ReasonCode.DURATION_SHORTFALL
        assert "duration" in result.explanation.lower()

    def test_distance_shortfall(self):
        """Test when distance is below threshold."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=None,
            distance_km=10.0,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=None,
            distance_meters=5000.0,  # 5km, below 80% of 10km
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.PARTIAL
        assert result.reason_code == ReasonCode.DISTANCE_SHORTFALL
        assert "distance" in result.explanation.lower()

    def test_both_duration_and_distance_shortfall(self):
        """Test when both duration and distance are below threshold."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=60,
            distance_km=10.0,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,  # Below threshold
            distance_meters=5000.0,  # Below threshold
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.PARTIAL
        assert result.reason_code == ReasonCode.DURATION_AND_DISTANCE_SHORTFALL


class TestReconciliationSubstitution:
    """Test substitution scenarios."""

    def test_wrong_activity_type(self):
        """Test when activity type doesn't match planned type."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Ride",
            duration_seconds=30 * 60,
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.SUBSTITUTED
        assert result.matched_activity_id == "activity-1"
        assert result.confidence < 0.6
        assert result.reason_code == ReasonCode.WRONG_ACTIVITY_TYPE
        assert "substituted" in result.explanation.lower()

    def test_type_variations_match(self):
        """Test that type variations are recognized (e.g., Run vs Running)."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Running",
            duration_seconds=30 * 60,
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.COMPLETED
        assert result.reason_code == ReasonCode.EXACT_MATCH


class TestReconciliationMissed:
    """Test missed session scenarios."""

    def test_no_activity_found(self):
        """Test when no activity is found for planned session."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status=None,
        )

        results = reconcile_sessions([planned], [])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.MISSED
        assert result.matched_activity_id is None
        assert result.confidence == 1.0
        assert result.reason_code == ReasonCode.NO_ACTIVITY_FOUND
        assert "no activity" in result.explanation.lower()

    def test_activity_outside_time_window(self):
        """Test when activity exists but outside time tolerance."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status=None,
        )

        # Activity 25 hours before (outside 12h tolerance)
        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 14, 9, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.MISSED


class TestReconciliationMultipleActivities:
    """Test scenarios with multiple activities."""

    def test_multiple_activities_same_day_best_match_selected(self):
        """Test when multiple activities exist, best match is selected."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=5.0,
            intensity=None,
            status=None,
        )

        # Activity 1: Wrong type
        activity1 = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Ride",
            duration_seconds=30 * 60,
            distance_meters=5000.0,
            source="strava",
        )

        # Activity 2: Correct type and duration
        activity2 = CompletedActivityInput(
            activity_id="activity-2",
            start_time=datetime(2024, 1, 15, 18, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
            distance_meters=5000.0,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity1, activity2])

        assert len(results) == 1
        result = results[0]
        assert result.matched_activity_id == "activity-2"  # Best match selected
        assert result.status == SessionStatus.COMPLETED

    def test_one_activity_per_session_rule(self):
        """Test that one activity can only satisfy one session."""
        planned1 = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status=None,
        )

        planned2 = PlannedSessionInput(
            session_id="session-2",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status=None,
        )

        # Only one activity
        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned1, planned2], [activity])

        assert len(results) == 2

        # First session should match
        result1 = next(r for r in results if r.session_id == "session-1")
        assert result1.status == SessionStatus.COMPLETED
        assert result1.matched_activity_id == "activity-1"

        # Second session should be missed (activity already matched)
        result2 = next(r for r in results if r.session_id == "session-2")
        assert result2.status == SessionStatus.MISSED
        assert result2.matched_activity_id is None


class TestReconciliationSkipped:
    """Test skipped session scenarios."""

    def test_user_marked_skipped(self):
        """Test when session is explicitly marked as skipped."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status="skipped",
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.SKIPPED
        assert result.reason_code == ReasonCode.USER_MARKED_SKIPPED
        assert result.matched_activity_id is None

    def test_rest_day(self):
        """Test rest day handling."""
        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Rest",
            duration_minutes=None,
            distance_km=None,
            intensity=None,
            status=None,
        )

        results = reconcile_sessions([planned], [])

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.SKIPPED
        assert result.reason_code == ReasonCode.REST_DAY_OVERRIDE
        assert "rest day" in result.explanation.lower()


class TestReconciliationConfig:
    """Test configuration options."""

    def test_custom_duration_threshold(self):
        """Test custom duration threshold."""
        config = ReconciliationConfig(duration_threshold=0.5)  # 50% threshold

        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=60,
            distance_km=None,
            intensity=None,
            status=None,
        )

        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=35 * 60,  # 58% of planned - should pass with 50% threshold
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity], config=config)

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.COMPLETED  # Should pass with lower threshold

    def test_custom_time_tolerance(self):
        """Test custom time tolerance."""
        config = ReconciliationConfig(time_tolerance_hours=24)  # 24 hour tolerance

        planned = PlannedSessionInput(
            session_id="session-1",
            date=date(2024, 1, 15),
            type="Run",
            duration_minutes=30,
            distance_km=None,
            intensity=None,
            status=None,
        )

        # Activity 20 hours before (within 24h tolerance)
        activity = CompletedActivityInput(
            activity_id="activity-1",
            start_time=datetime(2024, 1, 14, 14, 0, tzinfo=UTC),
            type="Run",
            duration_seconds=30 * 60,
            distance_meters=None,
            source="strava",
        )

        results = reconcile_sessions([planned], [activity], config=config)

        assert len(results) == 1
        result = results[0]
        assert result.status == SessionStatus.COMPLETED  # Should match with larger tolerance
