"""Tests for week structure loader (B3).

Tests verify that week structure loading:
- Selects correct structure from RAG based on filtering criteria
- Maps day names to day indices correctly
- Maps session types to DayType enum correctly
- Raises error when no matching structure found
- Preserves rules, session_groups, and guards
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.coach.schemas.athlete_state import AthleteState
from app.planner.enums import DayType, PlanType, RaceDistance, TrainingIntent, WeekFocus
from app.planner.errors import InvalidSkeletonError
from app.planner.models import MacroWeek, PhilosophySelection, PlanContext, PlanRuntimeContext
from app.planner.week_structure import load_week_structure


@pytest.fixture
def ctx_5k_build() -> PlanContext:
    """Create a 5K build plan context for testing."""
    return PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=RaceDistance.FIVE_K,
        target_date="2025-06-15",
    )


@pytest.fixture
def runtime_ctx_daniels(ctx_5k_build: PlanContext) -> PlanRuntimeContext:
    """Create runtime context with Daniels philosophy."""
    philosophy = PhilosophySelection(
        philosophy_id="daniels",
        domain="running",
        audience="intermediate",
    )
    return PlanRuntimeContext(plan=ctx_5k_build, philosophy=philosophy)


@pytest.fixture
def intermediate_runner() -> AthleteState:
    """Create intermediate athlete state for testing."""
    return AthleteState(
        ctl=45.0,
        atl=40.0,
        tsb=5.0,
        load_trend="stable",
        volatility="low",
        days_since_rest=2,
        days_to_race=None,
        seven_day_volume_hours=5.0,
        fourteen_day_volume_hours=10.0,
        flags=[],
        confidence=0.9,
    )


def test_daniels_5k_build_structure_loaded(
    runtime_ctx_daniels: PlanRuntimeContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that Daniels 5K build structure is loaded correctly."""
    week = MacroWeek(week_index=1, focus=WeekFocus.BUILD, total_distance=50.0)
    days_to_race = 21

    ws = load_week_structure(
        ctx=runtime_ctx_daniels,
        week=week,
        athlete_state=intermediate_runner,
        days_to_race=days_to_race,
    )

    assert ws.philosophy_id == "daniels"
    assert ws.structure_id == "daniels__5k__intermediate__build__v1"
    assert ws.focus == WeekFocus.BUILD
    assert len(ws.days) == 7

    # Verify day indices are correct (0-6)
    day_indices = [d.day_index for d in ws.days]
    assert set(day_indices) == {0, 1, 2, 3, 4, 5, 6}

    # Verify exactly one long run exists
    long_days = [d for d in ws.days if d.day_type == DayType.LONG]
    assert len(long_days) == 1

    # Verify structure has rules, session_groups, guards
    assert ws.rules is not None
    assert "hard_days_max" in ws.rules
    assert ws.session_groups is not None
    assert "hard" in ws.session_groups
    assert ws.guards is not None


def test_structure_has_correct_day_types(
    runtime_ctx_daniels: PlanRuntimeContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that day types are mapped correctly from RAG session types."""
    week = MacroWeek(week_index=1, focus=WeekFocus.BUILD, total_distance=50.0)
    days_to_race = 21

    ws = load_week_structure(
        ctx=runtime_ctx_daniels,
        week=week,
        athlete_state=intermediate_runner,
        days_to_race=days_to_race,
    )

    # Verify we have easy, quality, and long days
    day_types = [d.day_type for d in ws.days]
    assert DayType.EASY in day_types
    assert DayType.QUALITY in day_types
    assert DayType.LONG in day_types


def test_no_matching_structure_raises(
    runtime_ctx_daniels: PlanRuntimeContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that no matching structure raises InvalidSkeletonError."""
    week = MacroWeek(week_index=1, focus=WeekFocus.BUILD, total_distance=50.0)
    days_to_race = 5  # Too few days - should not match any structure

    with pytest.raises(InvalidSkeletonError, match="No plan_structure found"):
        load_week_structure(
            ctx=runtime_ctx_daniels,
            week=week,
            athlete_state=intermediate_runner,
            days_to_race=days_to_race,
        )


def test_wrong_audience_raises(
    ctx_5k_build: PlanContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that wrong audience raises error."""
    # Create runtime context with wrong audience
    wrong_philosophy = PhilosophySelection(
        philosophy_id="daniels",
        domain="running",
        audience="advanced",  # Wrong audience
    )
    runtime_ctx = PlanRuntimeContext(plan=ctx_5k_build, philosophy=wrong_philosophy)
    week = MacroWeek(week_index=1, focus=WeekFocus.BUILD, total_distance=50.0)
    days_to_race = 21

    with pytest.raises(InvalidSkeletonError, match="No plan_structure found"):
        load_week_structure(
            ctx=runtime_ctx,
            week=week,
            athlete_state=intermediate_runner,
            days_to_race=days_to_race,
        )


def test_wrong_focus_raises(
    runtime_ctx_daniels: PlanRuntimeContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that wrong focus raises error."""
    week = MacroWeek(week_index=1, focus=WeekFocus.SHARPENING, total_distance=50.0)
    days_to_race = 21

    # SHARPENING might not exist for 5k intermediate, so this may raise
    # (depending on actual RAG coverage)
    with pytest.raises(InvalidSkeletonError, match="No plan_structure found"):
        load_week_structure(
            ctx=runtime_ctx_daniels,
            week=week,
            athlete_state=intermediate_runner,
            days_to_race=days_to_race,
        )


def test_race_distance_required(intermediate_runner: AthleteState) -> None:
    """Test that race_distance is required."""
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=None,  # Missing race_distance
        target_date="2025-06-15",
    )
    philosophy = PhilosophySelection(
        philosophy_id="daniels",
        domain="running",
        audience="intermediate",
    )
    runtime_ctx = PlanRuntimeContext(plan=ctx, philosophy=philosophy)
    week = MacroWeek(week_index=1, focus=WeekFocus.BUILD, total_distance=50.0)

    with pytest.raises(InvalidSkeletonError, match="Race distance is required"):
        load_week_structure(
            ctx=runtime_ctx,
            week=week,
            athlete_state=intermediate_runner,
            days_to_race=21,
        )


def test_days_sorted_by_index(
    runtime_ctx_daniels: PlanRuntimeContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that days are sorted by day_index."""
    week = MacroWeek(week_index=1, focus=WeekFocus.BUILD, total_distance=50.0)
    days_to_race = 21

    ws = load_week_structure(
        ctx=runtime_ctx_daniels,
        week=week,
        athlete_state=intermediate_runner,
        days_to_race=days_to_race,
    )

    day_indices = [d.day_index for d in ws.days]
    assert day_indices == sorted(day_indices)


def test_taper_structure_loaded(
    intermediate_runner: AthleteState,
) -> None:
    """Test that taper structure can be loaded."""
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=RaceDistance.FIVE_K,
        target_date="2025-06-15",
    )
    philosophy = PhilosophySelection(
        philosophy_id="daniels",
        domain="running",
        audience="intermediate",
    )
    runtime_ctx = PlanRuntimeContext(plan=ctx, philosophy=philosophy)
    week = MacroWeek(week_index=12, focus=WeekFocus.TAPER, total_distance=30.0)
    days_to_race = 7  # Within taper range

    ws = load_week_structure(
        ctx=runtime_ctx,
        week=week,
        athlete_state=intermediate_runner,
        days_to_race=days_to_race,
    )

    assert ws.focus == WeekFocus.TAPER
    assert len(ws.days) == 7
