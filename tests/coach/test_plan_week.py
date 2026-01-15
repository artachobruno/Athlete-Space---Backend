"""Tests for plan_week tool.

Tests that plan_week generates exactly 7 days of training sessions.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.coach.schemas.athlete_state import AthleteState
from app.coach.tools.plan_week import plan_week


@pytest.fixture
def mock_athlete_state() -> AthleteState:
    """Create a mock athlete state for testing."""
    return AthleteState(
        ctl=50.0,
        atl=45.0,
        tsb=5.0,
        seven_day_volume_hours=8.0,
        confidence=0.9,
        load_trend="stable",
        flags=set(),
    )


@pytest.fixture
def mock_user_id() -> str:
    """Mock user ID."""
    return "test_user_123"


@pytest.fixture
def mock_athlete_id() -> int:
    """Mock athlete ID."""
    return 456


@pytest.mark.asyncio
async def test_plan_week_generates_7_days(
    mock_athlete_state: AthleteState,
    mock_user_id: str,
    mock_athlete_id: int,
):
    """Test that plan_week generates exactly 7 days of training."""
    # Mock the dependencies
    with patch("app.coach.tools.plan_week._check_weekly_plan_exists", return_value=False), patch(
        "app.coach.tools.plan_week.build_training_summary"
    ) as mock_summary, patch(
        "app.coach.tools.plan_week.execute_canonical_pipeline"
    ) as mock_pipeline, patch(
        "app.coach.tools.plan_week.now_user"
    ) as mock_now_user, patch(
        "app.coach.tools.plan_week.to_utc"
    ) as mock_to_utc, patch(
        "app.coach.tools.plan_week.get_session"
    ) as mock_session:
        # Setup mocks
        from app.calendar.training_summary import TrainingSummary

        mock_summary.return_value = TrainingSummary(
            volume={"total_duration_minutes": 480},
            execution={"compliance_rate": 0.8, "completed_sessions": 5},
            load={"ctl": 50.0, "atl": 45.0, "tsb": 5.0},
            reliability_flags=MagicMock(high_variance=False),
        )

        # Mock user timezone
        mock_user = MagicMock()
        mock_user.timezone = "America/New_York"
        mock_session.return_value.__enter__.return_value.execute.return_value.first.return_value = (
            mock_user,
        )

        # Mock date calculations
        test_date = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)  # Monday
        mock_now_user.return_value = test_date
        mock_to_utc.side_effect = lambda x: x

        # Mock pipeline to return a week with 7 sessions
        from app.planner.calendar_persistence import PersistResult
        from app.planner.enums import DayType
        from app.planner.models import PlannedSession as PlannedSessionModel
        from app.planner.models import PlannedWeek

        # Create mock sessions (7 days)
        mock_sessions = [
            PlannedSessionModel(
                day_index=i,
                day_type=DayType.EASY if i % 2 == 0 else DayType.QUALITY,
                distance=5.0 + i,
                template=MagicMock(),
            )
            for i in range(7)
        ]

        mock_week = PlannedWeek(week_index=1, focus=MagicMock(), sessions=mock_sessions)
        mock_pipeline.return_value = (
            [mock_week],
            PersistResult(plan_id="test_plan", created=7, updated=0, skipped=0, warnings=[]),
        )

        # Execute plan_week
        result = await plan_week(
            state=mock_athlete_state,
            user_id=mock_user_id,
            athlete_id=mock_athlete_id,
        )

        # Verify result
        assert result is not None
        assert isinstance(result, str)
        assert "Weekly Training Plan Created" in result or "weekly plan" in result.lower()

        # Verify pipeline was called with correct context (1 week)
        mock_pipeline.assert_called_once()
        call_args = mock_pipeline.call_args
        ctx = call_args.kwargs["ctx"]
        assert ctx.weeks == 1
        assert ctx.plan_type.value == "week"

        # Verify that 7 sessions were created
        planned_weeks = mock_pipeline.return_value[0]
        assert len(planned_weeks) == 1
        assert len(planned_weeks[0].sessions) == 7


@pytest.mark.asyncio
async def test_plan_week_requires_user_and_athlete_id(mock_athlete_state: AthleteState):
    """Test that plan_week requires user_id and athlete_id."""
    # Missing user_id
    result = await plan_week(state=mock_athlete_state, user_id=None, athlete_id=123)
    assert "[CLARIFICATION]" in result or "required" in result.lower()

    # Missing athlete_id
    result = await plan_week(state=mock_athlete_state, user_id="user123", athlete_id=None)
    assert "[CLARIFICATION]" in result or "required" in result.lower()


@pytest.mark.asyncio
async def test_plan_week_idempotency_check(
    mock_athlete_state: AthleteState,
    mock_user_id: str,
    mock_athlete_id: int,
):
    """Test that plan_week returns early if weekly plan already exists."""
    with patch(
        "app.coach.tools.plan_week._check_weekly_plan_exists", return_value=True
    ) as mock_check:
        result = await plan_week(
            state=mock_athlete_state,
            user_id=mock_user_id,
            athlete_id=mock_athlete_id,
        )

        assert "already created" in result.lower() or "already exists" in result.lower()
        mock_check.assert_called_once_with(mock_user_id, mock_athlete_id)
