"""Unit tests for calendar reconciliation API layer.

Tests cover:
- _run_reconciliation_safe() behavior with MISSED status
- Season endpoint DB vs final status counters
"""

from datetime import UTC, date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from app.calendar.reconciliation import ReconciliationResult, SessionStatus


class TestReconciliationSafeMissedStatus:
    """Test _run_reconciliation_safe() preserves planned status for MISSED results."""

    @patch("app.calendar.api.reconcile_calendar")
    @patch("app.calendar.api.auto_match_sessions")
    def test_missed_status_does_not_override_planned(
        self,
        mock_auto_match: Mock,
        mock_reconcile: Mock,
    ):
        """Test that MISSED reconciliation result does NOT override planned status."""
        from app.calendar.api import _run_reconciliation_safe

        # Setup: Planned session with status="planned"
        session_id = "session-1"

        # Mock reconciliation result: MISSED with no matched_activity_id
        mock_reconcile.return_value = [
            ReconciliationResult(
                session_id=session_id,
                date="2024-01-15",
                status=SessionStatus.MISSED,
                matched_activity_id=None,
                confidence=1.0,
                reason_code=None,  # type: ignore
                explanation="No activity found",
            )
        ]

        # Run reconciliation
        reconciliation_map, matched_activity_ids = _run_reconciliation_safe(
            user_id="test-user",
            athlete_id=1,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )

        # Assert: MISSED status should NOT be in reconciliation_map
        # This preserves the DB status ("planned")
        assert session_id not in reconciliation_map, (
            "MISSED status should NOT override DB status. "
            "reconciliation_map should be empty to preserve planned status."
        )
        assert matched_activity_ids == set(), "No matched activities for MISSED status"

        # Verify reconcile_calendar was called
        mock_reconcile.assert_called_once()

        # Verify auto_match was NOT called (no matched activities)
        mock_auto_match.assert_called_once_with(
            user_id="test-user",
            reconciliation_results=mock_reconcile.return_value,
        )

    @patch("app.calendar.api.reconcile_calendar")
    @patch("app.calendar.api.auto_match_sessions")
    def test_completed_status_with_matched_activity_overrides(
        self,
        mock_auto_match: Mock,
        mock_reconcile: Mock,
    ):
        """Test that COMPLETED status with matched_activity_id DOES override DB status."""
        from app.calendar.api import _run_reconciliation_safe

        session_id = "session-1"
        activity_id = "activity-1"

        # Mock reconciliation result: COMPLETED with matched_activity_id
        mock_reconcile.return_value = [
            ReconciliationResult(
                session_id=session_id,
                date="2024-01-15",
                status=SessionStatus.COMPLETED,
                matched_activity_id=activity_id,
                confidence=1.0,
                reason_code=None,  # type: ignore
                explanation="Activity matched",
            )
        ]

        # Run reconciliation
        reconciliation_map, matched_activity_ids = _run_reconciliation_safe(
            user_id="test-user",
            athlete_id=1,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )

        # Assert: COMPLETED status SHOULD be in reconciliation_map
        assert reconciliation_map[session_id] == "completed"
        assert activity_id in matched_activity_ids

        mock_reconcile.assert_called_once()
        mock_auto_match.assert_called_once()

    @patch("app.calendar.api.reconcile_calendar")
    @patch("app.calendar.api.auto_match_sessions")
    def test_skipped_status_overrides(
        self,
        mock_auto_match: Mock,
        mock_reconcile: Mock,
    ):
        """Test that SKIPPED status DOES override DB status (user explicitly skipped)."""
        from app.calendar.api import _run_reconciliation_safe

        session_id = "session-1"

        # Mock reconciliation result: SKIPPED (no matched_activity_id)
        mock_reconcile.return_value = [
            ReconciliationResult(
                session_id=session_id,
                date="2024-01-15",
                status=SessionStatus.SKIPPED,
                matched_activity_id=None,
                confidence=1.0,
                reason_code=None,  # type: ignore
                explanation="User marked as skipped",
            )
        ]

        # Run reconciliation
        reconciliation_map, matched_activity_ids = _run_reconciliation_safe(
            user_id="test-user",
            athlete_id=1,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )

        # Assert: SKIPPED status SHOULD be in reconciliation_map
        assert reconciliation_map[session_id] == "skipped"
        assert matched_activity_ids == set()

        mock_reconcile.assert_called_once()
        mock_auto_match.assert_called_once()


class TestSeasonEndpointCounters:
    """Integration tests for season endpoint DB vs final status counters."""

    def test_planned_session_db_vs_final_counters(self, db_session):
        """Test that planned_sessions_db == planned_sessions_final for future planned session."""
        from datetime import datetime, timezone

        from sqlalchemy import select

        from app.calendar.api import get_season
        from app.db.models import PlannedSession, User
        from app.db.session import get_session

        # Create test user
        test_user_id = "test-user-season"
        user = User(
            id=test_user_id,
            email="test@example.com",
            timezone="UTC",
        )
        db_session.add(user)
        db_session.commit()

        # Create planned session in the future with status="planned"
        future_date = datetime.now(timezone.utc) + timedelta(days=7)
        planned_session = PlannedSession(
            id="planned-session-1",
            user_id=test_user_id,
            athlete_id=1,
            date=future_date,
            type="Run",
            title="Future Run",
            status="planned",
            completed=False,
            plan_type="single",
        )
        db_session.add(planned_session)
        db_session.commit()

        # Mock get_current_user_id to return test user
        with patch("app.calendar.api.get_current_user_id", return_value=test_user_id):
            # Mock _get_athlete_id to return None (no reconciliation if no athlete_id)
            with patch("app.calendar.api._get_athlete_id", return_value=None):
                # Call season endpoint
                response = get_season(user_id=test_user_id)

                # Assert: DB counters should match final counters for planned sessions
                assert response.planned_sessions_db == 1, "Should have 1 planned session in DB"
                assert response.planned_sessions_final == 1, "Should have 1 planned session final"
                assert response.planned_sessions_db == response.planned_sessions_final, (
                    "For future planned session with no reconciliation, "
                    "DB and final counts should match"
                )
                assert response.completed_sessions_db == 0, "Should have 0 completed sessions in DB"
                assert response.completed_sessions_final == 0, "Should have 0 completed sessions final"

    def test_planned_session_with_missed_reconciliation(self, db_session):
        """Test that MISSED reconciliation preserves planned count."""
        from datetime import datetime, timezone

        from app.calendar.reconciliation import ReconciliationResult, SessionStatus
        from sqlalchemy import select

        from app.calendar.api import _run_reconciliation_safe, get_season
        from app.db.models import PlannedSession, User
        from app.db.session import get_session

        # Create test user and athlete
        test_user_id = "test-user-missed"
        user = User(
            id=test_user_id,
            email="test@example.com",
            timezone="UTC",
        )
        db_session.add(user)
        db_session.commit()

        # Create planned session in the past (should be MISSED if no activity)
        past_date = datetime.now(timezone.utc) - timedelta(days=7)
        planned_session = PlannedSession(
            id="planned-session-missed",
            user_id=test_user_id,
            athlete_id=1,
            date=past_date,
            type="Run",
            title="Past Run",
            status="planned",
            completed=False,
            plan_type="single",
        )
        db_session.add(planned_session)
        db_session.commit()

        # Mock reconciliation to return MISSED
        with patch("app.calendar.api.reconcile_calendar") as mock_reconcile:
            mock_reconcile.return_value = [
                ReconciliationResult(
                    session_id=planned_session.id,
                    date=past_date.date().isoformat(),
                    status=SessionStatus.MISSED,
                    matched_activity_id=None,
                    confidence=1.0,
                    reason_code=None,  # type: ignore
                    explanation="No activity found",
                )
            ]

            # Mock get_current_user_id
            with patch("app.calendar.api.get_current_user_id", return_value=test_user_id):
                # Call season endpoint
                response = get_season(user_id=test_user_id)

                # Assert: MISSED should NOT reduce planned count
                # DB and final should both be 1 (MISSED preserves planned status)
                assert response.planned_sessions_db == 1, "Should have 1 planned session in DB"
                assert response.planned_sessions_final == 1, (
                    "MISSED reconciliation should preserve planned status, "
                    "so final count should still be 1"
                )
                assert response.planned_sessions_db == response.planned_sessions_final, (
                    "MISSED status should not change planned count"
                )