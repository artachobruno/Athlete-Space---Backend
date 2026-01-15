"""Tests for HR-based pace reconciliation.

Tests the conservative, explainability-first reconciliation logic
that compares planned workout intent vs observed HR zone.
"""

from datetime import UTC, datetime, timezone

import pytest

from app.athletes.models import AthletePaceProfile
from app.db.models import Activity, Athlete, PlannedSession
from app.plans.reconciliation.hr import map_hr_to_zone
from app.plans.reconciliation.reconcile import reconcile_workout
from app.plans.reconciliation.types import ExecutedWorkout


@pytest.fixture
def sample_hr_profile() -> dict[str, dict[str, int]]:
    """Sample HR zone profile for testing."""
    return {
        "z1": {"min": 100, "max": 120},
        "z2": {"min": 120, "max": 140},
        "lt1": {"min": 140, "max": 160},
        "lt2": {"min": 160, "max": 175},
        "threshold": {"min": 175, "max": 185},
        "vo2max": {"min": 185, "max": 200},
    }


@pytest.fixture
def sample_athlete_pace_profile(sample_hr_profile: dict[str, dict[str, int]]) -> AthletePaceProfile:
    """Sample athlete pace profile for testing."""
    return AthletePaceProfile(
        race_goal_pace_min_per_mile=8.0,
        hr_zones=sample_hr_profile,
    )


@pytest.fixture
def sample_planned_session_easy() -> PlannedSession:
    """Sample easy planned session."""
    return PlannedSession(
        id="test-session-1",
        user_id="test-user",
        athlete_id=1,
        date=datetime.now(UTC),
        type="Run",
        title="Easy Run",
        intent="easy",
        distance_mi=5.0,
        duration_minutes=40,
    )


@pytest.fixture
def sample_planned_session_quality() -> PlannedSession:
    """Sample quality planned session."""
    return PlannedSession(
        id="test-session-2",
        user_id="test-user",
        athlete_id=1,
        date=datetime.now(UTC),
        type="Run",
        title="Tempo Run",
        intent="quality",
        distance_mi=6.0,
        duration_minutes=42,
    )


@pytest.fixture
def sample_athlete() -> Athlete:
    """Sample athlete for testing."""
    return Athlete(
        id="test-athlete",
        user_id="test-user",
    )


class TestMapHrToZone:
    """Tests for HR zone mapping."""

    def test_map_hr_to_zone_z1(self, sample_hr_profile: dict[str, dict[str, int]]) -> None:
        """Test mapping HR to z1."""
        assert map_hr_to_zone(110, sample_hr_profile) == "z1"

    def test_map_hr_to_zone_z2(self, sample_hr_profile: dict[str, dict[str, int]]) -> None:
        """Test mapping HR to z2."""
        assert map_hr_to_zone(130, sample_hr_profile) == "z2"

    def test_map_hr_to_zone_threshold(self, sample_hr_profile: dict[str, dict[str, int]]) -> None:
        """Test mapping HR to threshold."""
        assert map_hr_to_zone(180, sample_hr_profile) == "threshold"

    def test_map_hr_to_zone_unknown(self, sample_hr_profile: dict[str, dict[str, int]]) -> None:
        """Test mapping HR outside zones returns unknown."""
        assert map_hr_to_zone(50, sample_hr_profile) == "unknown"
        assert map_hr_to_zone(250, sample_hr_profile) == "unknown"

    def test_map_hr_to_zone_boundary(self, sample_hr_profile: dict[str, dict[str, int]]) -> None:
        """Test mapping HR at zone boundaries."""
        assert map_hr_to_zone(120, sample_hr_profile) == "z2"  # Upper bound of z1, lower bound of z2
        assert map_hr_to_zone(140, sample_hr_profile) == "lt1"  # Upper bound of z2, lower bound of lt1


class TestReconcileWorkout:
    """Tests for workout reconciliation logic."""

    def test_easy_run_too_hard_detected(
        self,
        sample_planned_session_easy: PlannedSession,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that easy run with high HR is detected as too hard."""
        executed = ExecutedWorkout(
            planned_session_id=sample_planned_session_easy.id,
            actual_distance_miles=5.0,
            actual_duration_min=40,
            avg_hr=180,  # Threshold zone
            max_hr=185,
            avg_pace_min_per_mile=8.0,
        )

        result = reconcile_workout(
            planned_session=sample_planned_session_easy,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        assert result.effort_mismatch == "too_hard"
        assert result.hr_zone == "threshold"
        assert result.recommendation is not None
        assert "too fast" in result.recommendation.lower()

    def test_easy_run_on_target(
        self,
        sample_planned_session_easy: PlannedSession,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that easy run with appropriate HR is on target."""
        executed = ExecutedWorkout(
            planned_session_id=sample_planned_session_easy.id,
            actual_distance_miles=5.0,
            actual_duration_min=40,
            avg_hr=130,  # z2 zone
            max_hr=135,
            avg_pace_min_per_mile=8.0,
        )

        result = reconcile_workout(
            planned_session=sample_planned_session_easy,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        assert result.effort_mismatch == "on_target"
        assert result.hr_zone == "z2"

    def test_quality_run_on_target(
        self,
        sample_planned_session_quality: PlannedSession,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that quality run with appropriate HR is on target."""
        executed = ExecutedWorkout(
            planned_session_id=sample_planned_session_quality.id,
            actual_distance_miles=6.0,
            actual_duration_min=42,
            avg_hr=180,  # Threshold zone
            max_hr=190,
            avg_pace_min_per_mile=7.0,
        )

        result = reconcile_workout(
            planned_session=sample_planned_session_quality,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        assert result.effort_mismatch == "on_target"
        assert result.hr_zone == "threshold"

    def test_quality_run_too_easy_detected(
        self,
        sample_planned_session_quality: PlannedSession,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that quality run with low HR is detected as too easy."""
        executed = ExecutedWorkout(
            planned_session_id=sample_planned_session_quality.id,
            actual_distance_miles=6.0,
            actual_duration_min=42,
            avg_hr=130,  # z2 zone
            max_hr=135,
            avg_pace_min_per_mile=7.0,
        )

        result = reconcile_workout(
            planned_session=sample_planned_session_quality,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        assert result.effort_mismatch == "too_easy"
        assert result.hr_zone == "z2"
        assert result.recommendation is not None
        assert "stimulus" in result.recommendation.lower()

    def test_long_run_too_hard_detected(
        self,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that long run with high HR is detected as too hard."""
        planned_session = PlannedSession(
            id="test-session-3",
            user_id="test-user",
            athlete_id=1,
            date=datetime.now(UTC),
            type="Run",
            title="Long Run",
            intent="long",
            distance_mi=10.0,
            duration_minutes=80,
        )

        executed = ExecutedWorkout(
            planned_session_id=planned_session.id,
            actual_distance_miles=10.0,
            actual_duration_min=80,
            avg_hr=180,  # Threshold zone
            max_hr=185,
            avg_pace_min_per_mile=8.0,
        )

        result = reconcile_workout(
            planned_session=planned_session,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        assert result.effort_mismatch == "too_hard"
        assert result.hr_zone == "threshold"
        assert result.recommendation is not None
        assert "long run" in result.recommendation.lower()

    def test_long_run_allows_steady_mp_drift(
        self,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that long run allows steady/MP drift (lt2 zone)."""
        planned_session = PlannedSession(
            id="test-session-5",
            user_id="test-user",
            athlete_id=1,
            date=datetime.now(UTC),
            type="Run",
            title="Long Run",
            intent="long",
            distance_mi=12.0,
            duration_minutes=96,
        )

        executed = ExecutedWorkout(
            planned_session_id=planned_session.id,
            actual_distance_miles=12.0,
            actual_duration_min=96,
            avg_hr=170,  # lt2 zone (steady/MP equivalent)
            max_hr=175,
            avg_pace_min_per_mile=8.0,
        )

        result = reconcile_workout(
            planned_session=planned_session,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        # Long runs can drift into lt2 (steady/MP zones) - this is acceptable
        assert result.effort_mismatch == "on_target"
        assert result.hr_zone == "lt2"

    def test_long_run_allows_split_intent(
        self,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that long run allows split-intent (easy + MP finish)."""
        planned_session = PlannedSession(
            id="test-session-6",
            user_id="test-user",
            athlete_id=1,
            date=datetime.now(UTC),
            type="Run",
            title="Long Run with MP Finish",
            intent="long",
            distance_mi=14.0,
            duration_minutes=112,
        )

        # Average HR might be in lt1/lt2 range due to progression/MP finish
        executed = ExecutedWorkout(
            planned_session_id=planned_session.id,
            actual_distance_miles=14.0,
            actual_duration_min=112,
            avg_hr=165,  # lt1/lt2 boundary (acceptable for progression/MP finish)
            max_hr=175,
            avg_pace_min_per_mile=8.0,
        )

        result = reconcile_workout(
            planned_session=planned_session,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        # Split-intent long runs (easy + MP finish) are common and valid
        assert result.effort_mismatch == "on_target"
        assert result.hr_zone in {"lt1", "lt2"}

    def test_no_hr_data_returns_unknown(
        self,
        sample_planned_session_easy: PlannedSession,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that missing HR data returns unknown mismatch."""
        executed = ExecutedWorkout(
            planned_session_id=sample_planned_session_easy.id,
            actual_distance_miles=5.0,
            actual_duration_min=40,
            avg_hr=None,
            max_hr=None,
            avg_pace_min_per_mile=8.0,
        )

        result = reconcile_workout(
            planned_session=sample_planned_session_easy,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        assert result.effort_mismatch == "unknown"
        assert result.hr_zone is None

    def test_no_hr_profile_returns_unknown(
        self,
        sample_planned_session_easy: PlannedSession,
        sample_athlete: Athlete,
    ) -> None:
        """Test that missing HR profile returns unknown mismatch."""
        executed = ExecutedWorkout(
            planned_session_id=sample_planned_session_easy.id,
            actual_distance_miles=5.0,
            actual_duration_min=40,
            avg_hr=130,
            max_hr=135,
            avg_pace_min_per_mile=8.0,
        )

        result = reconcile_workout(
            planned_session=sample_planned_session_easy,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=None,
        )

        assert result.effort_mismatch == "unknown"
        assert result.hr_zone is None

    def test_rest_day_too_hard_detected(
        self,
        sample_athlete: Athlete,
        sample_athlete_pace_profile: AthletePaceProfile,
    ) -> None:
        """Test that rest day with activity is detected as too hard."""
        planned_session = PlannedSession(
            id="test-session-4",
            user_id="test-user",
            athlete_id=1,
            date=datetime.now(UTC),
            type="Run",
            title="Rest Day",
            intent="rest",
            distance_mi=None,
            duration_minutes=None,
        )

        executed = ExecutedWorkout(
            planned_session_id=sample_planned_session_easy.id,
            actual_distance_miles=3.0,
            actual_duration_min=25,
            avg_hr=150,  # lt1 zone
            max_hr=160,
            avg_pace_min_per_mile=8.3,
        )

        result = reconcile_workout(
            planned_session=planned_session,
            executed=executed,
            athlete=sample_athlete,
            athlete_pace_profile=sample_athlete_pace_profile,
        )

        assert result.effort_mismatch == "too_hard"
        assert result.hr_zone == "lt1"
        assert result.recommendation is not None
        assert "rest" in result.recommendation.lower()
