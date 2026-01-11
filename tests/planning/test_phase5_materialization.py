"""Phase 5 Session Materialization Tests.

Tests for deterministic session materialization.
All tests ensure invariants are preserved.
"""

import pytest

from app.planning.errors import PlanningInvariantError
from app.planning.library.session_template import SessionTemplate
from app.planning.materialization.expander import expand_template
from app.planning.materialization.materialize_week import materialize_week
from app.planning.materialization.materializer import materialize_session
from app.planning.materialization.models import ConcreteSession, IntervalBlock
from app.planning.materialization.pace import derive_distance_miles
from app.planning.materialization.validate import validate_materialized_sessions
from app.planning.output.models import MaterializedSession, WeekPlan


def create_test_template(
    template_id: str,
    session_type: str,
    intensity_level: str,
    min_duration: int,
    max_duration: int,
    warmup_min: int | None = None,
    cooldown_min: int | None = None,
    structure: dict[str, str | int | float] | None = None,
) -> SessionTemplate:
    """Helper to create test session templates."""
    from app.planning.library.session_template import SessionType

    type_map: dict[str, SessionType] = {
        "easy": "easy",
        "long": "long",
        "tempo": "tempo",
        "interval": "interval",
        "hills": "hills",
        "strides": "strides",
        "recovery": "recovery",
        "rest": "rest",
    }

    return SessionTemplate(
        id=template_id,
        name=f"Test {template_id}",
        session_type=type_map.get(session_type, "easy"),
        intensity_level=intensity_level,
        race_types=["5k", "10k", "half", "marathon"],
        phase_tags=["base", "build", "peak", "taper"],
        min_duration_min=min_duration,
        max_duration_min=max_duration,
        tags=[],
        warmup_min=warmup_min,
        cooldown_min=cooldown_min,
        structure=structure,
    )


def test_time_preserved_exactly():
    """Test that duration_minutes is preserved exactly during materialization."""
    template = create_test_template(
        template_id="easy_1",
        session_type="easy",
        intensity_level="easy",
        min_duration=30,
        max_duration=60,
    )

    session = MaterializedSession(
        day="mon",
        session_template_id="easy_1",
        session_type="easy",
        duration_minutes=45,
        distance_miles=5.0,
    )

    concrete = materialize_session(session, template, pace_min_per_mile=9.0)

    assert concrete.duration_minutes == 45
    assert concrete.duration_minutes == session.duration_minutes


def test_distance_derived_correctly():
    """Test that distance is derived correctly from duration and pace."""
    template = create_test_template(
        template_id="easy_1",
        session_type="easy",
        intensity_level="easy",
        min_duration=30,
        max_duration=60,
    )

    session = MaterializedSession(
        day="mon",
        session_template_id="easy_1",
        session_type="easy",
        duration_minutes=45,
        distance_miles=0.0,  # Will be recalculated
    )

    pace = 9.0  # 9 min/mile
    expected_distance = round(45 / 9.0, 2)  # 5.0 miles

    concrete = materialize_session(session, template, pace_min_per_mile=pace)

    assert concrete.distance_miles == expected_distance


def test_derive_distance_miles():
    """Test distance derivation function directly."""
    distance = derive_distance_miles(duration_min=60, pace_min_per_mile=8.0)
    assert distance == 7.5

    distance = derive_distance_miles(duration_min=45, pace_min_per_mile=9.0)
    assert distance == 5.0


def test_derive_distance_miles_invalid_pace():
    """Test distance derivation raises error for invalid pace."""
    with pytest.raises(ValueError, match="Invalid pace"):
        derive_distance_miles(duration_min=60, pace_min_per_mile=0.0)

    with pytest.raises(ValueError, match="Invalid pace"):
        derive_distance_miles(duration_min=60, pace_min_per_mile=-5.0)


def test_interval_scaling_deterministic():
    """Test that interval scaling is deterministic."""
    template = create_test_template(
        template_id="interval_1",
        session_type="interval",
        intensity_level="hard",
        min_duration=40,
        max_duration=60,
        warmup_min=10,
        cooldown_min=10,
        structure={
            "reps": 6,
            "work_min": 2.0,
            "rest_min": 1.0,
            "intensity": "5k pace",
        },
    )

    # Expand with different durations
    expanded_60 = expand_template(template, duration_min=60)
    expanded_45 = expand_template(template, duration_min=45)

    assert expanded_60.warmup_minutes == 10
    assert expanded_60.cooldown_minutes == 10
    assert expanded_45.warmup_minutes == 10
    assert expanded_45.cooldown_minutes == 10

    # Intervals should scale down if needed
    if expanded_45.intervals:
        assert expanded_45.intervals[0].reps <= expanded_60.intervals[0].reps if expanded_60.intervals else True


def test_warmup_cooldown_respected():
    """Test that warmup and cooldown are respected."""
    template = create_test_template(
        template_id="tempo_1",
        session_type="tempo",
        intensity_level="hard",
        min_duration=50,
        max_duration=70,
        warmup_min=15,
        cooldown_min=10,
    )

    session = MaterializedSession(
        day="tue",
        session_template_id="tempo_1",
        session_type="tempo",
        duration_minutes=60,
        distance_miles=7.0,
    )

    concrete = materialize_session(session, template, pace_min_per_mile=8.5)

    assert concrete.warmup_minutes == 15
    assert concrete.cooldown_minutes == 10


def test_expand_template_insufficient_time():
    """Test that expander handles insufficient time correctly."""
    template = create_test_template(
        template_id="tempo_1",
        session_type="tempo",
        intensity_level="hard",
        min_duration=50,
        max_duration=70,
        warmup_min=15,
        cooldown_min=10,
    )

    # Should drop cooldown if insufficient time (never warmup)
    expanded = expand_template(template, duration_min=20)
    assert expanded.warmup_minutes == 15
    assert expanded.cooldown_minutes is None


def test_expand_template_insufficient_time_error():
    """Test that expander raises error if even warmup doesn't fit."""
    template = create_test_template(
        template_id="tempo_1",
        session_type="tempo",
        intensity_level="hard",
        min_duration=50,
        max_duration=70,
        warmup_min=30,
        cooldown_min=10,
    )

    with pytest.raises(ValueError, match="Insufficient duration"):
        expand_template(template, duration_min=20)


def test_materialize_week_preserves_order():
    """Test that week materialization preserves session order."""
    templates = {
        "easy_1": create_test_template(
            template_id="easy_1",
            session_type="easy",
            intensity_level="easy",
            min_duration=30,
            max_duration=60,
        ),
        "long_1": create_test_template(
            template_id="long_1",
            session_type="long",
            intensity_level="easy",
            min_duration=90,
            max_duration=120,
        ),
    }

    week_plan = WeekPlan(
        week_index=0,
        sessions=[
            MaterializedSession(
                day="mon",
                session_template_id="easy_1",
                session_type="easy",
                duration_minutes=45,
                distance_miles=5.0,
            ),
            MaterializedSession(
                day="sun",
                session_template_id="long_1",
                session_type="long",
                duration_minutes=90,
                distance_miles=10.0,
            ),
        ],
        total_duration_min=135,
        total_distance_miles=15.0,
    )

    concrete_sessions = materialize_week(
        week_plan=week_plan,
        templates=templates,
        pace_min_per_mile=9.0,
    )

    assert len(concrete_sessions) == 2
    assert concrete_sessions[0].day == "mon"
    assert concrete_sessions[1].day == "sun"


def test_materialize_week_skips_rest_days():
    """Test that week materialization skips rest days."""
    templates = {
        "easy_1": create_test_template(
            template_id="easy_1",
            session_type="easy",
            intensity_level="easy",
            min_duration=30,
            max_duration=60,
        ),
    }

    week_plan = WeekPlan(
        week_index=0,
        sessions=[
            MaterializedSession(
                day="mon",
                session_template_id="easy_1",
                session_type="easy",
                duration_minutes=45,
                distance_miles=5.0,
            ),
            MaterializedSession(
                day="tue",
                session_template_id="UNASSIGNED",
                session_type="rest",
                duration_minutes=0,
                distance_miles=0.0,
            ),
        ],
        total_duration_min=45,
        total_distance_miles=5.0,
    )

    concrete_sessions = materialize_week(
        week_plan=week_plan,
        templates=templates,
        pace_min_per_mile=9.0,
    )

    assert len(concrete_sessions) == 1
    assert concrete_sessions[0].day == "mon"
    assert concrete_sessions[0].session_type == "easy"


def test_materialize_week_template_not_found():
    """Test that materialization raises error if template not found."""
    templates: dict[str, SessionTemplate] = {}

    week_plan = WeekPlan(
        week_index=0,
        sessions=[
            MaterializedSession(
                day="mon",
                session_template_id="missing_template",
                session_type="easy",
                duration_minutes=45,
                distance_miles=5.0,
            ),
        ],
        total_duration_min=45,
        total_distance_miles=5.0,
    )

    with pytest.raises(ValueError, match="Template not found"):
        materialize_week(
            week_plan=week_plan,
            templates=templates,
            pace_min_per_mile=9.0,
        )


def test_validate_materialized_sessions_time_preserved():
    """Test that validation checks time is preserved."""
    templates = {
        "easy_1": create_test_template(
            template_id="easy_1",
            session_type="easy",
            intensity_level="easy",
            min_duration=30,
            max_duration=60,
        ),
    }

    week_plan = WeekPlan(
        week_index=0,
        sessions=[
            MaterializedSession(
                day="mon",
                session_template_id="easy_1",
                session_type="easy",
                duration_minutes=45,
                distance_miles=5.0,
            ),
        ],
        total_duration_min=45,
        total_distance_miles=5.0,
    )

    concrete_sessions = materialize_week(
        week_plan=week_plan,
        templates=templates,
        pace_min_per_mile=9.0,
    )

    # Should pass validation
    validate_materialized_sessions(week_plan, concrete_sessions, race_type="5k")


def test_validate_materialized_sessions_time_mismatch():
    """Test that validation fails if time doesn't match."""
    week_plan = WeekPlan(
        week_index=0,
        sessions=[
            MaterializedSession(
                day="mon",
                session_template_id="easy_1",
                session_type="easy",
                duration_minutes=45,
                distance_miles=5.0,
            ),
        ],
        total_duration_min=45,
        total_distance_miles=5.0,
    )

    # Create concrete session with wrong duration
    concrete_session = ConcreteSession(
        day="mon",
        session_template_id="easy_1",
        session_type="easy",
        duration_minutes=50,  # Mismatch
        distance_miles=5.5,
    )

    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_materialized_sessions(
            week_plan=week_plan,
            concrete_sessions=[concrete_session],
            race_type="5k",
        )

    assert exc_info.value.code == "MATERIALIZATION_VALIDATION_FAILED"
    assert "Total duration mismatch" in str(exc_info.value.details)


def test_validate_materialized_sessions_long_run_count():
    """Test that validation checks long run count."""
    week_plan = WeekPlan(
        week_index=0,
        sessions=[
            MaterializedSession(
                day="sun",
                session_template_id="long_1",
                session_type="long",
                duration_minutes=90,
                distance_miles=10.0,
            ),
        ],
        total_duration_min=90,
        total_distance_miles=10.0,
    )

    # Create concrete session with wrong type
    concrete_session = ConcreteSession(
        day="sun",
        session_template_id="long_1",
        session_type="easy",  # Wrong type - no long run
        duration_minutes=90,
        distance_miles=10.0,
    )

    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_materialized_sessions(
            week_plan=week_plan,
            concrete_sessions=[concrete_session],
            race_type="5k",
        )

    assert exc_info.value.code == "MATERIALIZATION_VALIDATION_FAILED"
    assert "Long run count mismatch" in str(exc_info.value.details)


def test_idempotency():
    """Test that materialization is idempotent (same input produces same output)."""
    template = create_test_template(
        template_id="easy_1",
        session_type="easy",
        intensity_level="easy",
        min_duration=30,
        max_duration=60,
    )

    session = MaterializedSession(
        day="mon",
        session_template_id="easy_1",
        session_type="easy",
        duration_minutes=45,
        distance_miles=5.0,
    )

    # Materialize twice
    concrete1 = materialize_session(session, template, pace_min_per_mile=9.0)
    concrete2 = materialize_session(session, template, pace_min_per_mile=9.0)

    assert concrete1.duration_minutes == concrete2.duration_minutes
    assert concrete1.distance_miles == concrete2.distance_miles
    assert concrete1.warmup_minutes == concrete2.warmup_minutes
    assert concrete1.cooldown_minutes == concrete2.cooldown_minutes
