"""Contract test: Plan creation visibility to evaluation.

This test verifies that when a plan is created via plan_week,
the evaluate_plan_change function can see the planned sessions.

This is a plumbing/contract test, NOT a behavior test.
"""

from __future__ import annotations

import pytest

from datetime import UTC, date, datetime

from app.coach.schemas.athlete_state import AthleteState
from app.coach.tools.plan_week import plan_week
from app.db.models import StravaAccount, User
from app.tools.semantic.evaluate_plan_change import evaluate_plan_change


def create_test_user(db_session, user_id: str, athlete_id: int) -> str:
    """Helper to create a test user."""
    user = User(
        id=user_id,
        email=f"{user_id}@example.com",
        password_hash=None,
        auth_provider="password",
        created_at=datetime.now(UTC),
    )
    db_session.add(user)

    # Create StravaAccount for athlete_id mapping
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


@pytest.mark.asyncio
async def test_week_plan_visible_to_evaluation(db_session, today: date):
    """
    Contract test:
    If a plan is created, evaluate_plan_change MUST see planned sessions.
    This test does NOT assert behavior or decisions.
    """
    user_id = create_test_user(db_session, "test-user-visibility", 1)

    # Create a minimal AthleteState for plan_week
    # This is a minimal valid state - the actual values don't matter for this test
    state = AthleteState(
        ctl=30.0,
        atl=25.0,
        tsb=5.0,
        load_trend="stable",
        volatility="low",
        days_since_rest=1,
        days_to_race=None,
        seven_day_volume_hours=5.0,
        fourteen_day_volume_hours=10.0,
        flags=[],
        confidence=0.8,
    )

    # 1. Create a week plan using the REAL semantic tool
    await plan_week(
        state=state,
        user_id=user_id,
        athlete_id=1,
        user_feedback=None,
    )

    # 2. Run evaluation
    result = await evaluate_plan_change(
        user_id=user_id,
        athlete_id=1,
        horizon="week",
        today=today,
    )

    # 3. Assert visibility only
    assert result.current_state.total_planned_sessions > 0, (
        f"Expected > 0 planned sessions, but found {result.current_state.total_planned_sessions}. "
        f"Summary: {result.current_state_summary}"
    )
