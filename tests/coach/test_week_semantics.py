"""Contract test: Planning Model B+ week semantics.

Verifies that week plans include past + future days in the same calendar week,
and that planned_elapsed + planned_remaining == planned_total_week.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

pytestmark = pytest.mark.contract

from app.db.models import PlannedSession, StravaAccount, User
from app.tools.semantic.evaluate_plan_change import evaluate_plan_change
from app.utils.calendar import week_end, week_start


def _create_test_user(db_session, user_id: str, athlete_id: int) -> str:
    """Helper to create a test user."""
    user = User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash=None,
        auth_provider="password",
        created_at=datetime.now(UTC),
    )
    db_session.add(user)
    account = StravaAccount(
        user_id=user_id,
        athlete_id=str(athlete_id),
        access_token="test_token",
        refresh_token="test_refresh",
        expires_at=2147483647,
        created_at=datetime.now(UTC),
    )
    db_session.add(account)
    db_session.commit()
    return user_id


@pytest.fixture
def today() -> date:
    """Today's date for testing."""
    return date(2026, 1, 23)  # Thursday


def _create_planned_sessions_for_week(
    db_session, user_id: str, today: date, count: int = 5
) -> None:
    """Create planned sessions in [week_start(today), week_end(today)]."""
    start = week_start(today)
    end = week_end(today)
    for i in range(min(count, (end - start).days + 1)):
        d = start + timedelta(days=i)
        session = PlannedSession(
            user_id=user_id,
            sport="run",
            session_type="easy",
            title=f"Easy Run {i}",
            duration_seconds=3600,
            starts_at=datetime.combine(d, datetime.min.time()).replace(tzinfo=UTC),
            status="planned",
        )
        db_session.add(session)
    db_session.commit()


def test_week_plan_includes_past_and_future_days(
    db_session,
    today: date,
):
    """Week plan spans full calendar week; elapsed + remaining = total."""
    user_id = _create_test_user(db_session, "test-user-week-semantics", 1)
    _create_planned_sessions_for_week(db_session, user_id, today)

    result = evaluate_plan_change(
        user_id=user_id,
        athlete_id=1,
        horizon="week",
        today=today,
    )

    assert result.current_state.planned_total_week > 0, (
        f"Expected > 0 planned sessions in week, "
        f"got {result.current_state.planned_total_week}. "
        f"Summary: {result.current_state_summary}"
    )
    assert (
        result.current_state.planned_elapsed + result.current_state.planned_remaining
        == result.current_state.planned_total_week
    ), (
        f"planned_elapsed + planned_remaining must equal planned_total_week; "
        f"got {result.current_state.planned_elapsed} + "
        f"{result.current_state.planned_remaining} != "
        f"{result.current_state.planned_total_week}"
    )
