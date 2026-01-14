import pytest

from app.planning.schema.session_spec import Intensity, SessionSpec, SessionType, Sport


def test_session_spec_validate_volume_with_distance():
    spec = SessionSpec(
        sport=Sport.RUN,
        session_type=SessionType.TEMPO,
        intensity=Intensity.TEMPO,
        target_distance_km=10.0,
        target_duration_min=None,
        goal="tempo development",
        phase="base",
        week_number=1,
        day_of_week=0,
    )
    spec.validate_volume()


def test_session_spec_validate_volume_with_duration():
    spec = SessionSpec(
        sport=Sport.RUN,
        session_type=SessionType.EASY,
        intensity=Intensity.EASY,
        target_distance_km=None,
        target_duration_min=60,
        goal="easy run",
        phase="base",
        week_number=1,
        day_of_week=1,
    )
    spec.validate_volume()


def test_session_spec_validate_volume_fails_without_volume():
    spec = SessionSpec(
        sport=Sport.RUN,
        session_type=SessionType.EASY,
        intensity=Intensity.EASY,
        target_distance_km=None,
        target_duration_min=None,
        goal="easy run",
        phase="base",
        week_number=1,
        day_of_week=1,
    )
    with pytest.raises(ValueError, match="must include distance or duration"):
        spec.validate_volume()
