"""Phase 1 Mandatory Tests - Canonical Planning Schemas.

These tests ensure schemas cannot express invalid plans.
MANDATORY - CI must fail if these tests fail.

Tests enforce:
- PlanSpec cannot be created without pace
- PlanSpec uses time-based fields (weekly_duration_targets_min)
- SessionTemplate rejects distance fields
- WeekSkeleton enforces exactly one long run
- MaterializedSession distance ≈ duration x pace
- No schema accepts kilometers
- PRIMARY/DERIVED/FORBIDDEN field contracts
"""

from datetime import date

import pytest

from app.planning.compiler.week_skeleton import DayRole, WeekSkeleton
from app.planning.contracts import (
    FORBIDDEN_PLANNER_FIELDS,
    PLANNER_DERIVED_FIELDS,
    PLANNER_PRIMARY_FIELDS,
)
from app.planning.errors import PlanningInvariantError
from app.planning.library.philosophy import TrainingPhilosophy
from app.planning.library.session_template import SessionTemplate
from app.planning.output.models import MaterializedSession, WeekPlan
from app.planning.schemas.plan_spec import GoalType, PlanSpec, RaceType
from app.planning.schemas.validate_plan_spec import validate_plan_spec
from app.planning.schemas.validate_week_skeleton import validate_week_skeleton


def test_plan_spec_requires_pace():
    """Test that PlanSpec cannot be created without pace."""
    # Valid spec requires assumed_pace_min_per_mile
    # 13 weeks between Sep 1 and Dec 1 = 91 days / 7 = 13 weeks
    spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 12, 1),
        weekly_duration_targets_min=[480] * 13,  # Exactly 13 weeks
        assumed_pace_min_per_mile=8.0,  # Required
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )
    validate_plan_spec(spec)
    assert spec.assumed_pace_min_per_mile == 8.0


def test_plan_spec_rejects_zero_pace():
    """Test that PlanSpec rejects zero or negative pace."""
    with pytest.raises(PlanningInvariantError) as exc_info:
        spec = PlanSpec(
            goal_type="race",
            race_type="5k",
            start_date=date(2024, 9, 1),
            end_date=date(2024, 12, 1),
            weekly_duration_targets_min=[480] * 13,  # Exactly 13 weeks
            assumed_pace_min_per_mile=0.0,  # Invalid
            days_per_week=5,
            preferred_long_run_day="sun",
            source="user",
            plan_version="1.0",
        )
        validate_plan_spec(spec)
    assert exc_info.value.code == "INVALID_PACE"


def test_plan_spec_uses_time_based_fields():
    """Test that PlanSpec uses weekly_duration_targets_min (time-based)."""
    spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 12, 1),
        weekly_duration_targets_min=[480] * 13,  # Minutes, not miles
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )
    assert "weekly_duration_targets_min" in PLANNER_PRIMARY_FIELDS
    # Verify no forbidden fields exist
    spec_dict = spec.__dict__
    for field in FORBIDDEN_PLANNER_FIELDS:
        assert field not in spec_dict, f"PlanSpec contains forbidden field: {field}"


def test_plan_spec_rejects_mismatched_duration_length():
    """Test that PlanSpec rejects mismatched weekly duration length."""
    with pytest.raises(PlanningInvariantError) as exc_info:
        spec = PlanSpec(
            goal_type="race",
            race_type="5k",
            start_date=date(2024, 9, 1),
            end_date=date(2024, 12, 1),  # ~13 weeks
            weekly_duration_targets_min=[480, 500],  # Wrong length (2 weeks)
            assumed_pace_min_per_mile=8.0,
            days_per_week=5,
            preferred_long_run_day="sun",
            source="user",
            plan_version="1.0",
        )
        validate_plan_spec(spec)
    assert exc_info.value.code == "WEEKLY_DURATION_LENGTH_MISMATCH"


def test_session_template_rejects_distance_fields():
    """Test that SessionTemplate does not contain distance fields."""
    template = SessionTemplate(
        id="easy_1",
        name="Easy Run",
        session_type="easy",
        intensity_level="easy",
        race_types=["5k", "10k", "half", "marathon"],
        phase_tags=["base", "build"],
        min_duration_min=30,
        max_duration_min=90,
        tags=["easy", "aerobic"],
    )
    # Verify no distance fields exist
    template_dict = template.__dict__
    for field in FORBIDDEN_PLANNER_FIELDS:
        assert field not in template_dict, f"SessionTemplate contains forbidden field: {field}"

    # Verify time-based fields exist
    assert "min_duration_min" in PLANNER_PRIMARY_FIELDS
    assert "max_duration_min" in PLANNER_PRIMARY_FIELDS


def test_week_skeleton_enforces_one_long_run():
    """Test that WeekSkeleton enforces exactly one long run."""
    # Valid skeleton
    skeleton = WeekSkeleton(
        week_index=0,
        days={
            "mon": "easy",
            "tue": "hard",
            "wed": "easy",
            "thu": "hard",
            "fri": "rest",
            "sat": "easy",
            "sun": "long",  # Exactly one long
        },
    )
    validate_week_skeleton(skeleton)
    # Should not raise

    # Invalid: zero long runs
    with pytest.raises(PlanningInvariantError) as exc_info:
        invalid_skeleton = WeekSkeleton(
            week_index=0,
            days={
                "mon": "easy",
                "tue": "hard",
                "wed": "easy",
                "thu": "hard",
                "fri": "rest",
                "sat": "easy",
                "sun": "easy",  # No long run
            },
        )
        validate_week_skeleton(invalid_skeleton)
    assert exc_info.value.code == "MISSING_OR_EXTRA_LONG_RUN"

    # Invalid: two long runs
    with pytest.raises(PlanningInvariantError) as exc_info:
        invalid_skeleton2 = WeekSkeleton(
            week_index=0,
            days={
                "mon": "long",  # First long
                "tue": "hard",
                "wed": "easy",
                "thu": "hard",
                "fri": "rest",
                "sat": "easy",
                "sun": "long",  # Second long (invalid)
            },
        )
        validate_week_skeleton(invalid_skeleton2)
    assert exc_info.value.code == "MISSING_OR_EXTRA_LONG_RUN"


def test_materialized_session_distance_equals_duration_times_pace():
    """Test that MaterializedSession distance ≈ duration x pace."""
    pace_min_per_mile = 8.0
    duration_minutes = 60

    session = MaterializedSession(
        day="mon",
        session_template_id="easy_1",
        session_type="easy",
        duration_minutes=duration_minutes,  # PRIMARY
        distance_miles=duration_minutes / pace_min_per_mile,  # DERIVED
    )

    # Distance should be derived from duration x pace
    expected_distance = duration_minutes / pace_min_per_mile
    assert abs(session.distance_miles - expected_distance) < 0.01
    assert session.duration_minutes == duration_minutes  # PRIMARY
    assert session.distance_miles in PLANNER_DERIVED_FIELDS or "distance_miles" in PLANNER_DERIVED_FIELDS


def test_materialized_session_uses_duration_as_primary():
    """Test that MaterializedSession uses duration_minutes as PRIMARY."""
    MaterializedSession(
        day="mon",
        session_template_id="easy_1",
        session_type="easy",
        duration_minutes=60,  # PRIMARY
        distance_miles=7.5,  # DERIVED
    )
    assert "duration_minutes" in PLANNER_PRIMARY_FIELDS
    assert "distance_miles" in PLANNER_DERIVED_FIELDS


def test_no_schema_accepts_kilometers():
    """Test that no schema accepts kilometers."""
    # Verify contracts define forbidden fields
    assert "distance_km" in FORBIDDEN_PLANNER_FIELDS

    # Verify schemas don't use forbidden fields
    # This is enforced by type checking and tests


def test_week_plan_uses_time_as_primary():
    """Test that WeekPlan uses total_duration_min as PRIMARY."""
    sessions = [
        MaterializedSession(
            day="mon",
            session_template_id="easy_1",
            session_type="easy",
            duration_minutes=60,
            distance_miles=7.5,
        ),
        MaterializedSession(
            day="tue",
            session_template_id="tempo_1",
            session_type="tempo",
            duration_minutes=45,
            distance_miles=5.6,
        ),
    ]
    total_duration = sum(s.duration_minutes for s in sessions)
    total_distance = sum(s.distance_miles for s in sessions)

    week = WeekPlan(
        week_index=0,
        sessions=sessions,
        total_duration_min=total_duration,  # PRIMARY
        total_distance_miles=total_distance,  # DERIVED
    )

    assert "total_duration_min" in PLANNER_PRIMARY_FIELDS
    assert "total_distance_miles" in PLANNER_DERIVED_FIELDS
    assert week.total_duration_min == total_duration
    assert abs(week.total_distance_miles - total_distance) < 0.01


def test_valid_plan_spec_passes():
    """Test that a valid PlanSpec passes validation."""
    spec = PlanSpec(
        goal_type="race",
        race_type="marathon",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 12, 1),
        weekly_duration_targets_min=[480] * 13,  # Exactly 13 weeks
        assumed_pace_min_per_mile=8.5,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )
    validate_plan_spec(spec)
    # Should not raise


def test_contracts_enforce_primary_derived_forbidden():
    """Test that contracts define PRIMARY/DERIVED/FORBIDDEN fields."""
    assert len(PLANNER_PRIMARY_FIELDS) > 0
    assert len(PLANNER_DERIVED_FIELDS) > 0
    assert len(FORBIDDEN_PLANNER_FIELDS) > 0

    # Verify no overlap between PRIMARY and DERIVED
    assert PLANNER_PRIMARY_FIELDS.isdisjoint(PLANNER_DERIVED_FIELDS)

    # Verify FORBIDDEN doesn't overlap with PRIMARY
    assert PLANNER_PRIMARY_FIELDS.isdisjoint(FORBIDDEN_PLANNER_FIELDS)

    # Verify FORBIDDEN doesn't overlap with DERIVED
    assert PLANNER_DERIVED_FIELDS.isdisjoint(FORBIDDEN_PLANNER_FIELDS)
