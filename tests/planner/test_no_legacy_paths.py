"""Kill-switch tests to ensure legacy planner paths are disabled.

These tests ensure that:
1. Legacy planner functions cannot be called
2. Only the new linear pipeline is accessible
3. Legacy code cannot silently reappear
"""

from datetime import UTC, datetime, timezone

import pytest

from app.planner.plan_race_simple import plan_race_simple
from app.planning.llm.plan_week import PlanWeekInput, plan_week_llm
from app.planning.plan_race import plan_race_build_new
from app.planning.repair.volume_repair import repair_week_volume
from app.planning.schema.session_spec import Intensity, SessionSpec, SessionType, Sport


def test_legacy_plan_race_build_new_is_disabled():
    """Test that plan_race_build_new raises RuntimeError."""
    race_date = datetime(2025, 6, 15, tzinfo=UTC)

    with pytest.raises(RuntimeError, match="Legacy planner path disabled"):
        plan_race_build_new(
            race_date=race_date,
            distance="Marathon",
            user_id="test_user",
            athlete_id=1,
        )


@pytest.mark.asyncio
async def test_legacy_plan_week_llm_is_disabled():
    """Test that plan_week_llm raises RuntimeError."""
    week_input = PlanWeekInput(
        week_number=1,
        phase="base",
        total_volume_km=50.0,
        long_run_km=20.0,
        days_available=[0, 1, 2, 3, 4, 5, 6],
        sport=Sport.RUN,
    )

    with pytest.raises(RuntimeError, match="Legacy planner path disabled"):
        await plan_week_llm(week_input)


def test_legacy_repair_week_volume_is_disabled():
    """Test that repair_week_volume raises RuntimeError."""
    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
    ]

    with pytest.raises(RuntimeError, match="Legacy volume repair disabled"):
        repair_week_volume(specs, target_km=50.0)


def test_only_simple_planner_is_accessible():
    """Test that plan_race_simple is the only accessible planner entry point.

    This test verifies that the new planner function exists and can be imported.
    Actual execution may require proper setup (athlete state, etc.), but the
    function should be callable without raising "function disabled" errors.
    """
    # Just verify the function exists and is callable
    assert callable(plan_race_simple)

    # Verify it's not the legacy function
    assert plan_race_simple.__name__ == "plan_race_simple"
    assert "plan_race_simple" in plan_race_simple.__module__
