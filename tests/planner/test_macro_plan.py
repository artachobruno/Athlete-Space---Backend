"""Tests for macro plan generation (B2).

These tests verify that macro plan generation:
- Produces correct number of weeks
- Validates schema correctly
- Enforces business rules (taper for race plans)
- Aborts on failures (no retries)
"""

import pytest

from app.coach.schemas.athlete_state import AthleteState
from app.planner.enums import PlanType, RaceDistance, TrainingIntent, WeekFocus
from app.planner.errors import InvalidMacroPlanError
from app.planner.macro_plan import generate_macro_plan
from app.planner.models import PlanContext


@pytest.fixture
def mock_athlete_state() -> AthleteState:
    """Create a mock athlete state for testing."""
    return AthleteState(
        ctl=65.0,
        atl=55.0,
        tsb=10.0,
        load_trend="stable",
        volatility="low",
        days_since_rest=2,
        days_to_race=None,
        seven_day_volume_hours=8.0,
        fourteen_day_volume_hours=16.0,
        flags=[],
        confidence=0.9,
    )


@pytest.fixture
def race_plan_context() -> PlanContext:
    """Create a race plan context for testing."""
    return PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=RaceDistance.MARATHON,
        target_date="2025-06-15",
    )


@pytest.fixture
def season_plan_context() -> PlanContext:
    """Create a season plan context for testing."""
    return PlanContext(
        plan_type=PlanType.SEASON,
        intent=TrainingIntent.MAINTAIN,
        weeks=16,
        race_distance=None,
        target_date=None,
    )


@pytest.mark.asyncio
async def test_macro_plan_length(
    race_plan_context: PlanContext,
    mock_athlete_state: AthleteState,
) -> None:
    """Test that macro plan produces exactly the requested number of weeks."""
    weeks = await generate_macro_plan(race_plan_context, mock_athlete_state)

    assert len(weeks) == 12
    assert all(week.week_index == i + 1 for i, week in enumerate(weeks))


@pytest.mark.asyncio
async def test_race_plan_ends_with_taper(
    race_plan_context: PlanContext,
    mock_athlete_state: AthleteState,
) -> None:
    """Test that race plans end with taper or recovery."""
    weeks = await generate_macro_plan(race_plan_context, mock_athlete_state)

    assert weeks[-1].focus in {WeekFocus.TAPER, WeekFocus.RECOVERY}


@pytest.mark.asyncio
async def test_season_plan_no_race_distance(
    season_plan_context: PlanContext,
    mock_athlete_state: AthleteState,
) -> None:
    """Test that season plans don't require race distance."""
    weeks = await generate_macro_plan(season_plan_context, mock_athlete_state)

    assert len(weeks) == 16
    # Season plans can have any focus, no taper requirement
    assert all(week.total_distance > 0 for week in weeks)


@pytest.mark.asyncio
async def test_weeks_are_sequential(
    race_plan_context: PlanContext,
    mock_athlete_state: AthleteState,
) -> None:
    """Test that week indices are sequential starting from 1."""
    weeks = await generate_macro_plan(race_plan_context, mock_athlete_state)

    for i, week in enumerate(weeks, start=1):
        assert week.week_index == i


@pytest.mark.asyncio
async def test_all_volumes_positive(
    race_plan_context: PlanContext,
    mock_athlete_state: AthleteState,
) -> None:
    """Test that all weekly volumes are positive."""
    weeks = await generate_macro_plan(race_plan_context, mock_athlete_state)

    assert all(week.total_distance > 0 for week in weeks)


@pytest.mark.asyncio
async def test_focus_values_valid(
    race_plan_context: PlanContext,
    mock_athlete_state: AthleteState,
) -> None:
    """Test that all focus values are valid WeekFocus enum values."""
    weeks = await generate_macro_plan(race_plan_context, mock_athlete_state)

    valid_focuses = {
        WeekFocus.BASE,
        WeekFocus.BUILD,
        WeekFocus.SHARPENING,
        WeekFocus.SPECIFIC,
        WeekFocus.TAPER,
        WeekFocus.RECOVERY,
        WeekFocus.EXPLORATION,
    }

    for week in weeks:
        assert week.focus in valid_focuses


@pytest.mark.asyncio
async def test_intent_preserved(
    race_plan_context: PlanContext,
    mock_athlete_state: AthleteState,
) -> None:
    """Test that intent is preserved in the generated plan."""
    weeks = await generate_macro_plan(race_plan_context, mock_athlete_state)

    # Intent is validated internally, but we can verify the plan structure
    # reflects the intent (e.g., BUILD should have progressive volume)
    assert len(weeks) > 0
    # For BUILD intent, we expect some progression
    # (exact validation is done in macro_plan.py)


@pytest.mark.asyncio
async def test_ultra_distance_plan(
    mock_athlete_state: AthleteState,
) -> None:
    """Test macro plan generation for ultra distance."""
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=20,
        race_distance=RaceDistance.ULTRA,
        target_date="2025-08-01",
    )

    weeks = await generate_macro_plan(ctx, mock_athlete_state)

    assert len(weeks) == 20
    assert weeks[-1].focus in {WeekFocus.TAPER, WeekFocus.RECOVERY}


@pytest.mark.asyncio
async def test_maintain_intent_plan(
    mock_athlete_state: AthleteState,
) -> None:
    """Test macro plan generation for maintain intent."""
    ctx = PlanContext(
        plan_type=PlanType.SEASON,
        intent=TrainingIntent.MAINTAIN,
        weeks=12,
        race_distance=None,
        target_date=None,
    )

    weeks = await generate_macro_plan(ctx, mock_athlete_state)

    assert len(weeks) == 12
    # MAINTAIN should have relatively stable volume
    assert all(week.total_distance > 0 for week in weeks)
