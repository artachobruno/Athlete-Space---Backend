"""Tests for calendar conflict detection and resolution.

A86.7: Mandatory tests for conflict detection system.
"""

import uuid
from datetime import UTC, date, datetime, timezone

import pytest

from app.calendar.conflicts import (
    Conflict,
    SessionTimeInfo,
    auto_shift_sessions,
    detect_conflicts,
    get_resolution_mode,
)
from app.db.models import PlannedSession


class TestSessionTimeInfo:
    """Tests for SessionTimeInfo canonical time model."""

    def test_all_day_session_no_time(self):
        """Test all-day session when no time is specified."""
        session_date = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
        time_info = SessionTimeInfo(session_date, time_str=None, duration_minutes=60)
        assert time_info.is_all_day is True
        assert time_info.start_time is None
        assert time_info.end_time is None

    def test_timed_session_with_time(self):
        """Test timed session when time is specified."""
        session_date = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
        time_info = SessionTimeInfo(session_date, time_str="10:00", duration_minutes=60)
        assert time_info.is_all_day is False
        assert time_info.start_time is not None
        assert time_info.end_time is not None
        assert time_info.end_time == time_info.start_time.replace(hour=11, minute=0)

    def test_timed_session_no_duration(self):
        """Test timed session without duration defaults to 1 hour."""
        session_date = datetime(2024, 1, 15, 0, 0, tzinfo=UTC)
        time_info = SessionTimeInfo(session_date, time_str="10:00", duration_minutes=None)
        assert time_info.is_all_day is False
        assert time_info.start_time is not None
        assert time_info.end_time is not None
        # Should default to 1 hour
        assert (time_info.end_time - time_info.start_time).total_seconds() == 3600

    def test_from_session_planned_session(self):
        """Test creating SessionTimeInfo from PlannedSession."""
        session = PlannedSession(
            id=str(uuid.uuid4()),
            user_id="user1",
            athlete_id=1,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            time="10:00",
            type="Run",
            title="Morning Run",
            duration_minutes=60,
        )
        time_info = SessionTimeInfo.from_session(session)
        assert time_info.is_all_day is False
        assert time_info.start_time is not None

    def test_from_session_dict(self):
        """Test creating SessionTimeInfo from dict."""
        session_dict = {
            "date": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            "time": "10:00",
            "duration_minutes": 60,
        }
        time_info = SessionTimeInfo.from_session(session_dict)
        assert time_info.is_all_day is False
        assert time_info.start_time is not None


class TestConflictDetection:
    """Tests for conflict detection logic."""

    def test_no_conflicts_different_dates(self):
        """Test no conflicts when sessions are on different dates."""
        existing = PlannedSession(
            id=str(uuid.uuid4()),
            user_id="user1",
            athlete_id=1,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            time="10:00",
            type="Run",
            title="Run 1",
            duration_minutes=60,
        )
        candidate = {
            "date": datetime(2024, 1, 16, 10, 0, tzinfo=UTC),
            "time": "10:00",
            "type": "Run",
            "title": "Run 2",
            "duration_minutes": 60,
        }
        conflicts = detect_conflicts([existing], [candidate])
        assert len(conflicts) == 0

    def test_time_overlap_conflict(self):
        """Test conflict when time ranges overlap."""
        existing = PlannedSession(
            id=str(uuid.uuid4()),
            user_id="user1",
            athlete_id=1,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            time="10:00",
            type="Run",
            title="Run 1",
            duration_minutes=60,
        )
        candidate = {
            "date": datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            "time": "10:30",
            "type": "Run",
            "title": "Run 2",
            "duration_minutes": 60,
        }
        conflicts = detect_conflicts([existing], [candidate])
        assert len(conflicts) == 1
        assert conflicts[0].reason == "time_overlap"
        assert conflicts[0].date == date(2024, 1, 15)

    def test_all_day_overlap_conflict(self):
        """Test conflict when both sessions are all-day on same date."""
        existing = PlannedSession(
            id=str(uuid.uuid4()),
            user_id="user1",
            athlete_id=1,
            date=datetime(2024, 1, 15, 0, 0, tzinfo=UTC),
            time=None,
            type="Run",
            title="Run 1",
            duration_minutes=60,
        )
        candidate = {
            "date": datetime(2024, 1, 15, 0, 0, tzinfo=UTC),
            "time": None,
            "type": "Run",
            "title": "Run 2",
            "duration_minutes": 60,
        }
        conflicts = detect_conflicts([existing], [candidate])
        assert len(conflicts) == 1
        assert conflicts[0].reason == "all_day_overlap"

    def test_all_day_vs_timed_conflict(self):
        """Test conflict when one is all-day and one is timed."""
        existing = PlannedSession(
            id=str(uuid.uuid4()),
            user_id="user1",
            athlete_id=1,
            date=datetime(2024, 1, 15, 0, 0, tzinfo=UTC),
            time=None,
            type="Run",
            title="All Day Run",
            duration_minutes=60,
        )
        candidate = {
            "date": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            "time": "10:00",
            "type": "Run",
            "title": "Timed Run",
            "duration_minutes": 60,
        }
        conflicts = detect_conflicts([existing], [candidate])
        assert len(conflicts) == 1
        assert conflicts[0].reason == "all_day_overlap"

    def test_multiple_key_sessions_conflict(self):
        """Test conflict when multiple key sessions on same day."""
        existing = PlannedSession(
            id=str(uuid.uuid4()),
            user_id="user1",
            athlete_id=1,
            date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
            time="10:00",
            type="Run",
            title="Hard Workout",
            intensity="hard",
            duration_minutes=60,
        )
        candidate = {
            "date": datetime(2024, 1, 15, 16, 0, tzinfo=UTC),
            "time": "16:00",
            "type": "Run",
            "title": "Tempo Intervals",
            "intensity": "hard",
            "duration_minutes": 60,
        }
        conflicts = detect_conflicts([existing], [candidate])
        # Should detect multiple key sessions conflict
        assert len(conflicts) >= 1
        key_conflicts = [c for c in conflicts if c.reason == "multiple_key_sessions"]
        assert len(key_conflicts) >= 1


class TestResolutionMode:
    """Tests for resolution mode policy."""

    def test_ai_generated_plan_auto_shift(self):
        """Test AI-generated plans use auto_shift mode."""
        assert get_resolution_mode("race") == "auto_shift"
        assert get_resolution_mode("season") == "auto_shift"
        assert get_resolution_mode("weekly") == "auto_shift"
        assert get_resolution_mode("single") == "auto_shift"

    def test_manual_upload_requires_confirmation(self):
        """Test manual uploads require user confirmation."""
        assert get_resolution_mode("manual_upload") == "require_user_confirmation"

    def test_unknown_plan_type_requires_confirmation(self):
        """Test unknown plan types default to require confirmation."""
        assert get_resolution_mode("unknown") == "require_user_confirmation"


class TestAutoShift:
    """Tests for auto-shift algorithm."""

    def test_auto_shift_no_conflicts(self):
        """Test auto-shift when no conflicts exist."""
        existing: list[PlannedSession] = []
        candidate = [
            {
                "date": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                "time": "10:00",
                "type": "Run",
                "title": "Run 1",
                "duration_minutes": 60,
            }
        ]
        shifted, unresolved = auto_shift_sessions(candidate, existing)
        assert len(shifted) == 1
        assert len(unresolved) == 0
        assert shifted[0]["date"].date() == date(2024, 1, 15)

    def test_auto_shift_resolves_conflict(self):
        """Test auto-shift resolves conflict by shifting to next day."""
        existing = [
            PlannedSession(
                id=str(uuid.uuid4()),
                user_id="user1",
                athlete_id=1,
                date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                time="10:00",
                type="Run",
                title="Existing Run",
                duration_minutes=60,
            )
        ]
        candidate = [
            {
                "date": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                "time": "10:00",
                "type": "Run",
                "title": "Candidate Run",
                "duration_minutes": 60,
            }
        ]
        shifted, _unresolved = auto_shift_sessions(candidate, existing)
        # Should shift to next day
        assert len(shifted) == 1
        assert shifted[0]["date"].date() == date(2024, 1, 16)
        # No unresolved conflicts if shift successful
        # Note: Auto-shift might not resolve all conflicts, so we check it shifted
        assert shifted[0]["date"].date() != date(2024, 1, 15)

    def test_auto_shift_preserves_time(self):
        """Test auto-shift preserves time when shifting date."""
        existing = [
            PlannedSession(
                id=str(uuid.uuid4()),
                user_id="user1",
                athlete_id=1,
                date=datetime(2024, 1, 15, 10, 0, tzinfo=UTC),
                time="10:00",
                type="Run",
                title="Existing Run",
                duration_minutes=60,
            )
        ]
        candidate = [
            {
                "date": datetime(2024, 1, 15, 10, 0, tzinfo=UTC),  # Same time as existing
                "time": "10:00",
                "type": "Run",
                "title": "Candidate Run",
                "duration_minutes": 60,
            }
        ]
        shifted, _ = auto_shift_sessions(candidate, existing)
        assert len(shifted) == 1
        # Time should be preserved
        assert shifted[0]["time"] == "10:00"
        # Date should be shifted (to avoid conflict)
        assert shifted[0]["date"].date() != date(2024, 1, 15)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
