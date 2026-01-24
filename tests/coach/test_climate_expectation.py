"""Unit tests for climate expectation helper (generate_climate_expectation)."""

from __future__ import annotations

import pytest

from app.coach.utils.climate_expectation import generate_climate_expectation


def _mk(
    *,
    has_climate_data: bool = True,
    conditions_label: str = "Hot",
    sport: str = "run",
    duration_seconds: int = 2700,
    distance_meters: float = 10_000.0,
    metrics: dict | None = None,
    title: str | None = "Easy run",
    heat_stress_index: float | None = 0.75,
    effective_heat_stress_index: float | None = None,
):
    """Minimal Activity-like object for tests."""
    if metrics is None:
        metrics = {"streams_data": {"latlng": {"data": [[0.0, 0.0]]}}}
    eff = effective_heat_stress_index if effective_heat_stress_index is not None else heat_stress_index
    obj = type("_MockActivity", (), {})()
    obj.has_climate_data = has_climate_data
    obj.conditions_label = conditions_label
    obj.sport = sport
    obj.duration_seconds = duration_seconds
    obj.distance_meters = distance_meters
    obj.metrics = metrics
    obj.title = title
    obj.heat_stress_index = heat_stress_index
    obj.effective_heat_stress_index = eff
    return obj


def test_hot_humid_primary_and_detail():
    """Hot & humid → primary + detail (equivalency available)."""
    act = _mk(conditions_label="Hot & Humid", heat_stress_index=0.80)
    out = generate_climate_expectation(act)
    assert out is not None
    assert "Warm, humid" in out["primary"]
    assert "harder than pace" in out["primary"]
    assert out["detail"] is not None
    assert "10-20 sec/mi" in out["detail"]


def test_hot_only_primary_and_detail():
    """Hot only → primary + detail."""
    act = _mk(conditions_label="Hot", heat_stress_index=0.70)
    out = generate_climate_expectation(act)
    assert out is not None
    assert "Warm conditions" in out["primary"]
    assert "harder than pace" in out["primary"]
    assert out["detail"] is not None
    assert "10-20 sec/mi" in out["detail"]


def test_cool_primary_and_detail():
    """Cool → primary + detail (pace + duration for cool detail)."""
    act = _mk(conditions_label="Cool", heat_stress_index=0.10)
    out = generate_climate_expectation(act)
    assert out is not None
    assert "Cool, dry" in out["primary"]
    assert "efficient pacing" in out["primary"]
    assert out["detail"] is not None
    assert "5-10 sec/mi" in out["detail"]


def test_mild_null():
    """Mild → null."""
    act = _mk(conditions_label="Mild")
    out = generate_climate_expectation(act)
    assert out is None


def test_indoor_null():
    """Indoor (no GPS) → null."""
    act = _mk(metrics={})
    out = generate_climate_expectation(act)
    assert out is None


def test_indoor_empty_latlng_null():
    """Indoor (empty latlng) → null."""
    act = _mk(metrics={"streams_data": {"latlng": {"data": []}}})
    out = generate_climate_expectation(act)
    assert out is None


def test_interval_null():
    """Interval (title) → null."""
    act = _mk(title="Track interval workout")
    out = generate_climate_expectation(act)
    assert out is None


def test_race_null():
    """Race (title) → null."""
    act = _mk(title="5k race")
    out = generate_climate_expectation(act)
    assert out is None


def test_no_climate_data_null():
    """No climate data → null."""
    act = _mk(has_climate_data=False)
    out = generate_climate_expectation(act)
    assert out is None


def test_short_duration_null():
    """< 30 min → null."""
    act = _mk(duration_seconds=1200)
    out = generate_climate_expectation(act)
    assert out is None


def test_non_aerobic_null():
    """Swim (non run/ride) → null."""
    act = _mk(sport="swim")
    out = generate_climate_expectation(act)
    assert out is None
