"""Tests for volume repair engine.

Tests cover:
- Under-target repair (scales up adjustable sessions)
- Over-target repair (scales down adjustable sessions)
- Exact match (no-op)
- No adjustable sessions (raises error)
- Long run clamp enforced
"""

import pytest

from app.planning.repair.volume_repair import (
    RepairImpossibleError,
    compute_week_volume,
    repair_week_volume,
    volume_within_tolerance,
)
from app.planning.schema.session_spec import Intensity, SessionSpec, SessionType, Sport


def test_compute_week_volume():
    """Test computing total week volume."""
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
    ]

    volume = compute_week_volume(specs)
    assert volume == 30.0


def test_volume_within_tolerance():
    """Test volume tolerance check."""
    assert volume_within_tolerance(50.0, 50.0, tolerance=0.05) is True
    assert volume_within_tolerance(52.0, 50.0, tolerance=0.05) is True
    assert volume_within_tolerance(48.0, 50.0, tolerance=0.05) is True
    assert volume_within_tolerance(53.0, 50.0, tolerance=0.05) is False
    assert volume_within_tolerance(47.0, 50.0, tolerance=0.05) is False


def test_repair_under_target():
    """Test repairing volume when under target (scales up adjustable sessions)."""
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
    ]

    target_km = 50.0
    repaired = repair_week_volume(specs, target_km)

    final_volume = compute_week_volume(repaired)
    assert abs(final_volume - target_km) < 0.2

    long_run = next(s for s in repaired if s.session_type == SessionType.LONG)
    assert long_run.target_distance_km is not None
    assert abs(long_run.target_distance_km - 20.0) <= 20.0 * 0.05


def test_repair_over_target():
    """Test repairing volume when over target (scales down adjustable sessions)."""
    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=15.0,
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
            target_distance_km=15.0,
            goal="easy run",
            phase="base",
            week_number=1,
            day_of_week=2,
        ),
    ]

    target_km = 40.0
    repaired = repair_week_volume(specs, target_km)

    final_volume = compute_week_volume(repaired)
    assert abs(final_volume - target_km) < 0.2

    long_run = next(s for s in repaired if s.session_type == SessionType.LONG)
    assert long_run.target_distance_km is not None
    assert abs(long_run.target_distance_km - 20.0) <= 20.0 * 0.05


def test_repair_exact_match():
    """Test repair with exact match (no-op)."""
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
    ]

    target_km = 40.0
    original_volumes = [s.target_distance_km for s in specs]
    repaired = repair_week_volume(specs, target_km)

    assert repaired == specs
    final_volumes = [s.target_distance_km for s in repaired]
    assert final_volumes == original_volumes


def test_repair_no_adjustable_sessions():
    """Test repair fails when no adjustable sessions available."""
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
            session_type=SessionType.TEMPO,
            intensity=Intensity.TEMPO,
            target_distance_km=10.0,
            goal="tempo run",
            phase="base",
            week_number=1,
            day_of_week=2,
        ),
    ]

    target_km = 50.0

    with pytest.raises(RepairImpossibleError, match="No adjustable sessions"):
        repair_week_volume(specs, target_km)


def test_repair_long_run_clamp():
    """Test that long run distances are clamped to ±5% of original."""
    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=5.0,
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
    ]

    target_km = 100.0
    original_long_run = specs[1].target_distance_km

    repaired = repair_week_volume(specs, target_km)

    long_run = next(s for s in repaired if s.session_type == SessionType.LONG)
    assert long_run.target_distance_km is not None
    assert long_run.target_distance_km >= original_long_run * 0.95
    assert long_run.target_distance_km <= original_long_run * 1.05


def test_repair_with_recovery_sessions():
    """Test repair includes recovery sessions as adjustable."""
    specs = [
        SessionSpec(
            sport=Sport.RUN,
            session_type=SessionType.RECOVERY,
            intensity=Intensity.VERY_EASY,
            target_distance_km=5.0,
            goal="recovery run",
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
    ]

    target_km = 50.0
    repaired = repair_week_volume(specs, target_km)

    final_volume = compute_week_volume(repaired)
    assert abs(final_volume - target_km) < 0.2

    recovery = next(s for s in repaired if s.session_type == SessionType.RECOVERY)
    easy = next(s for s in repaired if s.session_type == SessionType.EASY)

    assert recovery.target_distance_km is not None
    assert easy.target_distance_km is not None
    assert recovery.target_distance_km != 5.0 or easy.target_distance_km != 10.0
