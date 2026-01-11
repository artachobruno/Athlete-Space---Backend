"""Phase 2 Mandatory Tests - Deterministic Plan Compiler.

These tests ensure the Phase 2 compiler produces deterministic, invariant-safe WeekPlans.
MANDATORY - CI must fail if these tests fail.

Tests enforce:
1. Exactly one long run per week
2. Weekly duration matches target ± tolerance
3. No adjacent hard days
4. Long run ratio respected
5. Distance always derived from time
6. Compiler produces deterministic output
"""

from datetime import date, timedelta

import pytest

from app.planning.compiler.assemble_week import assemble_week_plan
from app.planning.compiler.compile_plan import compile_plan
from app.planning.compiler.skeleton_generator import generate_week_skeletons
from app.planning.compiler.spec_builder import build_plan_spec
from app.planning.compiler.time_allocator import allocate_week_time
from app.planning.compiler.validate_compiled_week import validate_compiled_week
from app.planning.errors import PlanningInvariantError
from app.planning.invariants import MAX_WEEKLY_TIME_DELTA_PCT
from app.planning.library.philosophy import TrainingPhilosophy
from app.planning.output.models import WeekPlan
from app.planning.schemas.plan_spec import PlanSpec


def _create_test_philosophy() -> TrainingPhilosophy:
    """Create a test TrainingPhilosophy."""
    return TrainingPhilosophy(
        id="test_philosophy",
        name="Test Philosophy",
        applicable_race_types=["5k", "10k", "half", "marathon"],
        max_hard_days_per_week=2,
        require_long_run=True,
        long_run_ratio_min=0.20,
        long_run_ratio_max=0.30,
        taper_weeks=1,
        taper_volume_reduction_pct=20.0,
        preferred_session_tags={"base": 1.0, "build": 0.8},
    )


def test_exactly_one_long_run_per_week():
    """Test that exactly one long run exists per week."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 15),  # 2 weeks
        weekly_duration_targets_min=[480, 500],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    weeks = compile_plan(plan_spec, philosophy)

    assert len(weeks) == 2
    for week in weeks:
        long_runs = [s for s in week.sessions if s.session_type == "long"]
        assert len(long_runs) == 1, f"Week {week.week_index} must have exactly one long run"


def test_weekly_duration_matches_target():
    """Test that weekly duration matches target within tolerance."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 15),  # 2 weeks
        weekly_duration_targets_min=[480, 500],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    weeks = compile_plan(plan_spec, philosophy)

    assert len(weeks) == 2
    for i, week in enumerate(weeks):
        target = plan_spec.weekly_duration_targets_min[i]
        actual = week.total_duration_min
        tolerance = int(target * MAX_WEEKLY_TIME_DELTA_PCT)
        assert abs(actual - target) <= tolerance, (
            f"Week {i}: actual={actual}, target={target}, tolerance={tolerance}"
        )


def test_no_adjacent_hard_days():
    """Test that hard days are not adjacent."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 15),  # 2 weeks
        weekly_duration_targets_min=[480, 500],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    weeks = compile_plan(plan_spec, philosophy)

    day_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

    for week in weeks:
        hard_days = [
            day_order.index(s.day) for s in week.sessions if s.session_type in ["tempo", "interval", "hills"]
        ]
        hard_days.sort()

        if len(hard_days) > 1:
            for i in range(len(hard_days) - 1):
                gap = hard_days[i + 1] - hard_days[i]
                assert gap > 1, f"Week {week.week_index}: hard days are adjacent"


def test_long_run_ratio_respected():
    """Test that long run ratio is respected."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 15),  # 2 weeks
        weekly_duration_targets_min=[480, 500],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    weeks = compile_plan(plan_spec, philosophy)

    for i, week in enumerate(weeks):
        long_run = next(s for s in week.sessions if s.session_type == "long")
        weekly_total = week.total_duration_min
        long_run_ratio = long_run.duration_minutes / weekly_total

        # Allow small tolerance for rounding (0.002) due to integer division
        assert (
            philosophy.long_run_ratio_min <= long_run_ratio <= philosophy.long_run_ratio_max + 0.002
        ), f"Week {i}: long_run_ratio={long_run_ratio:.3f} not in [{philosophy.long_run_ratio_min}, {philosophy.long_run_ratio_max}]"


def test_distance_always_derived_from_time():
    """Test that distance is always derived from time x pace."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 15),  # 2 weeks
        weekly_duration_targets_min=[480, 500],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    weeks = compile_plan(plan_spec, philosophy)

    for week in weeks:
        for session in week.sessions:
            expected_distance = round(session.duration_minutes / plan_spec.assumed_pace_min_per_mile, 2)
            assert abs(session.distance_miles - expected_distance) < 0.01, (
                f"Session {session.day}: distance={session.distance_miles}, "
                f"expected={expected_distance} (from {session.duration_minutes} min / {plan_spec.assumed_pace_min_per_mile} pace)"
            )


def test_compiler_produces_deterministic_output():
    """Test that compiler produces deterministic output."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 15),  # 2 weeks
        weekly_duration_targets_min=[480, 500],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    weeks1 = compile_plan(plan_spec, philosophy)
    weeks2 = compile_plan(plan_spec, philosophy)

    assert len(weeks1) == len(weeks2)

    for w1, w2 in zip(weeks1, weeks2, strict=True):
        assert w1.week_index == w2.week_index
        assert len(w1.sessions) == len(w2.sessions)
        assert w1.total_duration_min == w2.total_duration_min
        assert abs(w1.total_distance_miles - w2.total_distance_miles) < 0.01

        for s1, s2 in zip(w1.sessions, w2.sessions, strict=True):
            assert s1.day == s2.day
            assert s1.session_type == s2.session_type
            assert s1.duration_minutes == s2.duration_minutes
            assert abs(s1.distance_miles - s2.distance_miles) < 0.01


def test_spec_builder_resolves_anchors():
    """Test that spec_builder resolves all anchors."""
    start_date = date(2024, 9, 1)
    end_date = date(2024, 12, 1)  # ~13 weeks

    spec = build_plan_spec(
        goal_type="race",
        race_type="5k",
        start_date=start_date,
        end_date=end_date,
        assumed_pace_min_per_mile=8.0,
        recent_weekly_duration_min=480,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    assert spec.end_date == end_date
    assert len(spec.weekly_duration_targets_min) > 0
    assert all(d > 0 for d in spec.weekly_duration_targets_min)
    assert spec.assumed_pace_min_per_mile == 8.0


def test_spec_builder_rejects_none_end_date():
    """Test that spec_builder rejects None end_date."""
    with pytest.raises(ValueError, match="end_date must be resolved"):
        build_plan_spec(
            goal_type="race",
            race_type="5k",
            start_date=date(2024, 9, 1),
            end_date=None,
            assumed_pace_min_per_mile=8.0,
            recent_weekly_duration_min=480,
            days_per_week=5,
            preferred_long_run_day="sun",
            source="user",
            plan_version="1.0",
        )


def test_spec_builder_rejects_invalid_pace():
    """Test that spec_builder rejects invalid pace."""
    with pytest.raises(ValueError, match="Invalid assumed pace"):
        build_plan_spec(
            goal_type="race",
            race_type="5k",
            start_date=date(2024, 9, 1),
            end_date=date(2024, 12, 1),
            assumed_pace_min_per_mile=0.0,
            recent_weekly_duration_min=480,
            days_per_week=5,
            preferred_long_run_day="sun",
            source="user",
            plan_version="1.0",
        )


def test_skeleton_generator_creates_valid_structure():
    """Test that skeleton generator creates valid week structures."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 15),  # 2 weeks
        weekly_duration_targets_min=[480, 500],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    skeletons = generate_week_skeletons(plan_spec, philosophy)

    assert len(skeletons) == 2

    for skeleton in skeletons:
        long_count = sum(1 for role in skeleton.days.values() if role == "long")
        assert long_count == 1, f"Week {skeleton.week_index} must have exactly one long run"

        hard_count = sum(1 for role in skeleton.days.values() if role == "hard")
        assert hard_count <= philosophy.max_hard_days_per_week, (
            f"Week {skeleton.week_index} has too many hard days: {hard_count}"
        )

        active_days = sum(1 for role in skeleton.days.values() if role != "rest")
        assert active_days == plan_spec.days_per_week, (
            f"Week {skeleton.week_index} must have {plan_spec.days_per_week} active days, got {active_days}"
        )


def test_time_allocator_respects_weekly_target():
    """Test that time allocator respects weekly target."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 8),  # 1 week
        weekly_duration_targets_min=[480],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    skeletons = generate_week_skeletons(plan_spec, philosophy)
    skeleton = skeletons[0]

    allocation = allocate_week_time(skeleton, 480, philosophy)

    total = sum(allocation.values())
    tolerance = int(480 * MAX_WEEKLY_TIME_DELTA_PCT)
    assert abs(total - 480) <= tolerance, f"Total={total}, target=480, tolerance={tolerance}"


def test_validate_compiled_week_enforces_invariants():
    """Test that validate_compiled_week enforces Phase 0 invariants."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 8),  # 1 week
        weekly_duration_targets_min=[480],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    skeletons = generate_week_skeletons(plan_spec, philosophy)
    skeleton = skeletons[0]

    allocation = allocate_week_time(skeleton, 480, philosophy)

    # Should not raise
    validate_compiled_week(skeleton, allocation, 480, race_type="5k")


def test_assemble_week_creates_valid_week_plan():
    """Test that assemble_week creates valid WeekPlan."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="5k",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 9, 8),  # 1 week
        weekly_duration_targets_min=[480],
        assumed_pace_min_per_mile=8.0,
        days_per_week=5,
        preferred_long_run_day="sun",
        source="user",
        plan_version="1.0",
    )

    skeletons = generate_week_skeletons(plan_spec, philosophy)
    skeleton = skeletons[0]

    allocation = allocate_week_time(skeleton, 480, philosophy)

    week_plan = assemble_week_plan(
        week_index=0,
        allocation=allocation,
        skeleton=skeleton,
        pace_min_per_mile=plan_spec.assumed_pace_min_per_mile,
    )

    assert week_plan.week_index == 0
    assert len(week_plan.sessions) > 0
    assert week_plan.total_duration_min > 0
    assert week_plan.total_distance_miles > 0

    for session in week_plan.sessions:
        assert session.session_template_id == "UNASSIGNED"
        assert session.duration_minutes > 0
        assert session.distance_miles > 0

        # Verify distance is derived
        expected_distance = round(session.duration_minutes / plan_spec.assumed_pace_min_per_mile, 2)
        assert abs(session.distance_miles - expected_distance) < 0.01


def test_compile_plan_produces_valid_weeks():
    """Test that compile_plan produces valid weeks for multi-week plan."""
    philosophy = _create_test_philosophy()
    plan_spec = PlanSpec(
        goal_type="race",
        race_type="marathon",
        start_date=date(2024, 9, 1),
        end_date=date(2024, 12, 1),  # ~13 weeks
        weekly_duration_targets_min=[480] * 13,
        assumed_pace_min_per_mile=8.5,
        days_per_week=6,
        preferred_long_run_day="sat",
        source="user",
        plan_version="1.0",
    )

    weeks = compile_plan(plan_spec, philosophy)

    assert len(weeks) == 13

    for i, week in enumerate(weeks):
        assert week.week_index == i
        assert len(week.sessions) > 0

        # Verify all invariants
        long_runs = [s for s in week.sessions if s.session_type == "long"]
        assert len(long_runs) == 1

        target = plan_spec.weekly_duration_targets_min[i]
        tolerance = int(target * MAX_WEEKLY_TIME_DELTA_PCT)
        assert abs(week.total_duration_min - target) <= tolerance
