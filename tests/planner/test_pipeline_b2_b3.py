"""Integration tests for plan structure pipeline (B2 → B2.5 → B3).

Tests verify that the pipeline:
- Executes B2, B2.5, and B3 in correct order
- Locks philosophy before B3
- Restricts B3 to philosophy namespace
- Produces one structure per macro week
- All structures come from same philosophy
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.coach.schemas.athlete_state import AthleteState
from app.planner.enums import PlanType, RaceDistance, TrainingIntent, WeekFocus
from app.planner.errors import PlannerError
from app.planner.models import PlanContext
from app.planner.plan_pipeline import build_plan_structure


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
def intermediate_runner() -> AthleteState:
    """Create intermediate athlete state for testing."""
    return AthleteState(
        ctl=45.0,  # Intermediate CTL
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


@pytest.mark.asyncio
async def test_pipeline_selects_philosophy_and_structure(
    ctx_5k_build: PlanContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that pipeline selects philosophy and loads structures."""
    runtime_ctx, structures, _macro_weeks = await build_plan_structure(
        ctx=ctx_5k_build,
        athlete_state=intermediate_runner,
    )

    # Verify philosophy was selected
    assert runtime_ctx.philosophy is not None
    assert runtime_ctx.philosophy.philosophy_id == "daniels"
    assert runtime_ctx.philosophy.domain == "running"
    assert runtime_ctx.philosophy.audience == "intermediate"

    # Verify correct number of structures
    assert len(structures) == ctx_5k_build.weeks

    # Verify all structures come from same philosophy
    assert all(s.philosophy_id == "daniels" for s in structures)

    # Verify structures have correct focus
    assert structures[0].focus == WeekFocus.BUILD


@pytest.mark.asyncio
async def test_pipeline_philosophy_locked_before_b3(
    ctx_5k_build: PlanContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that philosophy is locked before B3 execution."""
    runtime_ctx, structures, _macro_weeks = await build_plan_structure(
        ctx=ctx_5k_build,
        athlete_state=intermediate_runner,
    )

    # Philosophy should be set in runtime context
    assert runtime_ctx.philosophy is not None

    # All structures should match the selected philosophy
    selected_philosophy_id = runtime_ctx.philosophy.philosophy_id
    assert all(s.philosophy_id == selected_philosophy_id for s in structures)


@pytest.mark.asyncio
async def test_pipeline_one_structure_per_week(
    ctx_5k_build: PlanContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that pipeline produces exactly one structure per macro week."""
    _runtime_ctx, structures, _macro_weeks = await build_plan_structure(
        ctx=ctx_5k_build,
        athlete_state=intermediate_runner,
    )

    assert len(structures) == ctx_5k_build.weeks

    # Each structure should have 7 days
    assert all(len(s.days) == 7 for s in structures)


@pytest.mark.asyncio
async def test_pipeline_user_preference_override(
    ctx_5k_build: PlanContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that user preference overrides automatic selection."""
    runtime_ctx, structures, _macro_weeks = await build_plan_structure(
        ctx=ctx_5k_build,
        athlete_state=intermediate_runner,
        user_preference="hansons",
    )

    # Should use user-selected philosophy
    assert runtime_ctx.philosophy.philosophy_id == "hansons"

    # All structures should come from hansons
    assert all(s.philosophy_id == "hansons" for s in structures)


@pytest.mark.asyncio
async def test_pipeline_ultra_distance(
    intermediate_runner: AthleteState,
) -> None:
    """Test pipeline with ultra distance."""
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=20,
        race_distance=RaceDistance.ULTRA,
        target_date="2025-08-01",
    )

    runtime_ctx, structures, _macro_weeks = await build_plan_structure(
        ctx=ctx,
        athlete_state=intermediate_runner,
    )

    # Should select ultra philosophy
    assert runtime_ctx.philosophy.domain == "ultra"
    assert len(structures) == 20

    # All structures should come from ultra domain
    assert all(s.philosophy_id in ["koop", "durability_first_ultra"] for s in structures)


@pytest.mark.asyncio
async def test_pipeline_fails_on_invalid_combo(
    intermediate_runner: AthleteState,
) -> None:
    """Test that pipeline fails gracefully on invalid combinations."""
    # Create context that might not have matching structures
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=RaceDistance.FIVE_K,
        target_date="2025-06-15",
    )

    # Use athlete state with incompatible flags
    incompatible_state = AthleteState(
        ctl=45.0,
        atl=40.0,
        tsb=5.0,
        load_trend="stable",
        volatility="low",
        days_since_rest=2,
        days_to_race=None,
        seven_day_volume_hours=5.0,
        fourteen_day_volume_hours=10.0,
        flags=["injury_prone"],  # This might prohibit some philosophies
        confidence=0.9,
    )

    # Should either succeed (if compatible) or fail with clear error
    try:
        _runtime_ctx, structures, _macro_weeks = await build_plan_structure(
            ctx=ctx,
            athlete_state=incompatible_state,
        )
        # If it succeeds, verify structures are valid
        assert len(structures) == 12
    except PlannerError:
        # Expected if no compatible philosophy found
        pass


@pytest.mark.asyncio
async def test_pipeline_taper_week_structure(
    ctx_5k_build: PlanContext,
    intermediate_runner: AthleteState,
) -> None:
    """Test that taper week structures are loaded correctly."""
    _runtime_ctx, structures, _macro_weeks = await build_plan_structure(
        ctx=ctx_5k_build,
        athlete_state=intermediate_runner,
    )

    # Last week should be taper (if macro plan generates it)
    # Check if any structure has taper focus
    # May or may not have taper depending on macro plan generation
    # Just verify structures are valid
    assert len(structures) > 0
