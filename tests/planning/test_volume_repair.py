"""Tests for volume repair engine.

LEGACY TESTS - Volume repair is disabled in B9.
These tests verify that repair_week_volume raises RuntimeError.
Utility functions (compute_week_volume, volume_within_tolerance) may still be used.
"""

import pytest

from app.planning.repair.volume_repair import (
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


def test_repair_under_target_is_disabled():
    """Test that repair_week_volume is disabled (B9)."""
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

    target_km = 50.0
    with pytest.raises(RuntimeError, match="Legacy volume repair disabled"):
        repair_week_volume(specs, target_km)


def test_repair_over_target_is_disabled():
    """Test that repair_week_volume is disabled (B9)."""
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
    ]

    target_km = 40.0
    with pytest.raises(RuntimeError, match="Legacy volume repair disabled"):
        repair_week_volume(specs, target_km)


def test_repair_exact_match_is_disabled():
    """Test that repair_week_volume is disabled (B9)."""
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
    with pytest.raises(RuntimeError, match="Legacy volume repair disabled"):
        repair_week_volume(specs, target_km)


def test_repair_no_adjustable_sessions_is_disabled():
    """Test that repair_week_volume is disabled (B9)."""
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

    with pytest.raises(RuntimeError, match="Legacy volume repair disabled"):
        repair_week_volume(specs, target_km)


def test_repair_long_run_clamp_is_disabled():
    """Test that repair_week_volume is disabled (B9)."""
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
    with pytest.raises(RuntimeError, match="Legacy volume repair disabled"):
        repair_week_volume(specs, target_km)


def test_repair_with_recovery_sessions_is_disabled():
    """Test that repair_week_volume is disabled (B9)."""
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
    with pytest.raises(RuntimeError, match="Legacy volume repair disabled"):
        repair_week_volume(specs, target_km)
