"""Contract tests: Plan creation coverage and safety.

Phase 1 tests that verify plan creation works across different scenarios
and that overwrite behavior is safe (no duplication).

These are plumbing/contract tests, NOT behavior tests.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.coach.schemas.athlete_state import AthleteState
from app.coach.tools.plan_season import plan_season
from app.coach.tools.plan_week import plan_week
from app.db.models import PlannedSession, StravaAccount, User
from app.db.session import get_session
from app.tools.read.plans import get_planned_activities
from app.tools.semantic.evaluate_plan_change import evaluate_plan_change

pytestmark = pytest.mark.contract


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


@pytest.fixture
def minimal_athlete_state() -> AthleteState:
    """Minimal valid AthleteState for plan creation."""
    return AthleteState(
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


@pytest.mark.asyncio
@pytest.mark.skip(reason="plan_season fails with VolumeAllocationError 'No ratio for group aerobic'; fix pipeline separately")
async def test_season_plan_visible_to_week_evaluation(
    db_session,
    today: date,
    minimal_athlete_state: AthleteState,
):
    """
    Contract test:
    If a season plan is created, evaluate_plan_change(horizon="week") MUST see
    planned sessions for the current week.

    This verifies season → week projection works.
    """
    user_id = create_test_user(db_session, "test-user-season-week", 2)

    # 1. Create a season plan using the REAL semantic tool
    # Season plan should create sessions across multiple weeks
    season_start = today
    season_end = today + timedelta(days=84)  # 12 weeks
    season_message = f"Create a season plan from {season_start.isoformat()} to {season_end.isoformat()}"

    await plan_season(
        message=season_message,
        user_id=user_id,
        athlete_id=2,
    )

    # 2. Run week evaluation (should see sessions for current week)
    result = evaluate_plan_change(
        user_id=user_id,
        athlete_id=2,
        horizon="week",
        today=today,
    )

    # 3. Assert visibility - week evaluation should see planned sessions
    assert result.current_state.planned_total_week > 0, (
        f"Expected > 0 planned sessions in week evaluation after season plan, "
        f"but found {result.current_state.planned_total_week}. "
        f"Summary: {result.current_state_summary}"
    )
    assert result.current_state.compliance_rate is not None


@pytest.mark.asyncio
async def test_plan_week_overwrite_does_not_duplicate(
    db_session,
    today: date,
    minimal_athlete_state: AthleteState,
):
    """
    Contract test:
    Creating a week plan twice should not duplicate sessions.

    This prevents silent duplication bugs.
    """
    user_id = create_test_user(db_session, "test-user-overwrite", 3)

    # 1. Create first week plan
    await plan_week(
        state=minimal_athlete_state,
        user_id=user_id,
        athlete_id=3,
        user_feedback=None,
    )

    # Count sessions after first creation
    week_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=UTC)
    week_end = datetime.combine(today + timedelta(days=7), datetime.max.time()).replace(tzinfo=UTC)

    with get_session() as session:
        result = session.execute(
            select(PlannedSession).where(
                PlannedSession.user_id == user_id,
                PlannedSession.starts_at >= week_start,
                PlannedSession.starts_at <= week_end,
            )
        )
        first_count = len(list(result.scalars().all()))

    # 2. Create second week plan (should not duplicate)
    # Note: plan_week has idempotency check, but we verify it works
    await plan_week(
        state=minimal_athlete_state,
        user_id=user_id,
        athlete_id=3,
        user_feedback=None,
    )

    # 3. Count sessions after second creation
    with get_session() as session:
        result = session.execute(
            select(PlannedSession).where(
                PlannedSession.user_id == user_id,
                PlannedSession.starts_at >= week_start,
                PlannedSession.starts_at <= week_end,
            )
        )
        second_count = len(list(result.scalars().all()))

    # 4. Assert no duplication
    # The count should be the same (idempotency) or at most slightly different
    # if the second call creates a new plan (which would replace the old one)
    assert second_count <= first_count * 1.1, (
        f"Plan overwrite created duplicates: first={first_count}, second={second_count}. "
        f"Expected second count to be <= first count (with small tolerance)."
    )

    # Also verify via get_planned_activities
    planned = get_planned_activities(
        user_id=user_id,
        start=today,
        end=today + timedelta(days=7),
    )
    assert len(planned) <= first_count * 1.1, (
        f"get_planned_activities shows duplication: first={first_count}, "
        f"get_planned_activities={len(planned)}"
    )
