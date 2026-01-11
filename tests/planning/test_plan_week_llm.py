from unittest.mock import AsyncMock, patch

import pytest

from app.planning.llm.plan_week import PlanWeekInput, plan_week_llm, validate_week
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
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=4,
        ),
    ]

    validate_week(specs, input_data)


def test_validate_week_volume_mismatch():
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
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
    ]

    with pytest.raises(ValueError, match="Week volume mismatch"):
        validate_week(specs, input_data)


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
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=2,
        ),
    ]

    with pytest.raises(ValueError, match="Week must contain exactly one long run"):
        validate_week(specs, input_data)


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
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=0,
        ),
    ]

    with pytest.raises(ValueError, match="SessionSpecs use invalid days"):
        validate_week(specs, input_data)
