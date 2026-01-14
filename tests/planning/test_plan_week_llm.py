from unittest.mock import AsyncMock, patch

import pytest

from app.planning.llm.plan_week import PlanWeekInput, plan_week_llm, validate_week
from app.planning.llm.week_skeleton import WeekSkeleton, generate_week_skeleton, validate_skeleton_match
from app.planning.schema.session_spec import Intensity, SessionSpec, SessionType, Sport


def test_validate_week_valid():
    input_data = PlanWeekInput(
        week_number=1,
        phase="base",
        total_volume_km=50.0,
        long_run_km=20.0,
        days_available=[0, 1, 2, 3, 4, 5, 6],
        sport=Sport.RUN,
    )

    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.LONG,
            intensity=Intensity.EASY,
            target_distance_km=20.0,
            target_duration_min=None,
            goal="long run",
            phase="base",
            week_number=1,
            day_of_week=5,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=2,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=4,
        ),
    ]

    validate_week(specs, input_data)


def test_validate_week_volume_mismatch_repairs():
    """Test that volume mismatch is repaired instead of failing."""
    input_data = PlanWeekInput(
        week_number=1,
        phase="base",
        total_volume_km=50.0,
        long_run_km=20.0,
        days_available=[0, 1, 2, 3, 4, 5, 6],
        sport=Sport.RUN,
    )

    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.LONG,
            intensity=Intensity.EASY,
            target_distance_km=20.0,
            target_duration_min=None,
            goal="long run",
            phase="base",
            week_number=1,
            day_of_week=5,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
    ]

    validate_week(specs, input_data)

    final_volume = sum(s.target_distance_km or 0.0 for s in specs)
    assert abs(final_volume - input_data.total_volume_km) < 0.2


def test_validate_week_no_long_run():
    input_data = PlanWeekInput(
        week_number=1,
        phase="base",
        total_volume_km=50.0,
        long_run_km=20.0,
        days_available=[0, 1, 2, 3, 4, 5, 6],
        sport=Sport.RUN,
    )

    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=25.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=25.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=2,
        ),
    ]

    with pytest.raises(ValueError, match="Week must contain exactly one long run"):
        validate_week(specs, input_data)


def test_generate_week_skeleton_has_exactly_one_long():
    """Regression test: skeleton generation guarantees exactly one long run."""
    input_data = PlanWeekInput(
        week_number=1,
        phase="base",
        total_volume_km=50.0,
        long_run_km=20.0,
        days_available=[0, 1, 2, 3, 4, 5, 6],
        sport=Sport.RUN,
    )

    skeleton = generate_week_skeleton(input_data)

    long_count = sum(1 for st in skeleton.days.values() if st == SessionType.LONG)
    assert long_count == 1, f"Skeleton must contain exactly one long run, got {long_count}"


def test_validate_skeleton_match_success():
    """Test that skeleton validation passes when specs match skeleton."""
    skeleton = WeekSkeleton(days={0: SessionType.EASY, 5: SessionType.LONG, 2: SessionType.EASY})

    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.LONG,
            intensity=Intensity.EASY,
            target_distance_km=20.0,
            target_duration_min=None,
            goal="long run",
            phase="base",
            week_number=1,
            day_of_week=5,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=2,
        ),
    ]

    # Should not raise
    validate_skeleton_match(specs, skeleton)


def test_validate_skeleton_match_missing_long():
    """Regression test: validation fails when LLM omits long run (current bug)."""
    skeleton = WeekSkeleton(days={0: SessionType.EASY, 5: SessionType.LONG, 2: SessionType.EASY})

    specs_no_long = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=10.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=2,
        ),
    ]

    # Should raise ValueError because skeleton requires long run on day 5
    with pytest.raises(ValueError, match="Skeleton requires session on day 5"):
        validate_skeleton_match(specs_no_long, skeleton)


def test_validate_week_invalid_day():
    input_data = PlanWeekInput(
        week_number=1,
        phase="base",
        total_volume_km=50.0,
        long_run_km=20.0,
        days_available=[0, 1, 2, 3, 4, 5],
        sport=Sport.RUN,
    )

    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.LONG,
            intensity=Intensity.EASY,
            target_distance_km=20.0,
            target_duration_min=None,
            goal="long run",
            phase="base",
            week_number=1,
            day_of_week=6,
        ),
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=30.0,
            target_duration_min=None,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
    ]

    with pytest.raises(ValueError, match="SessionSpecs use invalid days"):
        validate_week(specs, input_data)
