"""Tests for session template selector (B5).

Tests verify that template selection:
- Selects correct template set by phase + day_type
- Rotates deterministically based on week/day indices
- Fails if missing templates
- Maps day types to session types correctly
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.planner.enums import DayType, PlanType, RaceDistance, TrainingIntent
from app.planner.errors import TemplateSelectionError
from app.planner.models import (
    DistributedDay,
    PhilosophySelection,
    PlanContext,
    PlanRuntimeContext,
)
from app.planner.session_template_selector import (
    TemplateParseError,
    parse_template_file,
    select_template_for_day,
    select_templates_for_week,
)


@pytest.fixture
def ctx_5k_build() -> PlanContext:
    """Create a 5K build plan context for testing."""
    return PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=RaceDistance.FIVE_K,
        target_date="2025-06-15",
    )


@pytest.fixture
def runtime_ctx_daniels(ctx_5k_build: PlanContext) -> PlanRuntimeContext:
    """Create runtime context with Daniels philosophy."""
    philosophy = PhilosophySelection(
        philosophy_id="daniels",
        domain="running",
        audience="intermediate",
    )
    return PlanRuntimeContext(plan=ctx_5k_build, philosophy=philosophy)


def test_parse_template_file() -> None:
    """Test that template file parsing works correctly."""
    project_root = Path(__file__).parent.parent.parent.parent
    template_file = (
        project_root
        / "data"
        / "rag"
        / "planning"
        / "templates"
        / "running"
        / "daniels"
        / "daniels__5k__intermediate__build__threshold__v1.md"
    )

    template_set = parse_template_file(template_file)

    assert template_set.domain == "running"
    assert template_set.philosophy_id == "daniels"
    assert template_set.phase == "build"
    assert template_set.session_type == "threshold"
    assert template_set.audience == "intermediate"
    assert "5k" in template_set.race_types
    assert len(template_set.templates) == 2

    # Check first template
    template1 = template_set.templates[0]
    assert template1.template_id in ["cruise_intervals_v1", "steady_T_block_v1"]
    assert template1.kind in ["cruise_intervals", "steady_T_block"]
    assert "threshold" in template1.tags


def test_parse_template_file_missing_frontmatter() -> None:
    """Test that missing frontmatter raises error."""
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("No frontmatter here\n")
        temp_path = Path(f.name)

    try:
        with pytest.raises(TemplateParseError) as exc_info:
            parse_template_file(temp_path)
        assert "MISSING_FRONTMATTER" in str(exc_info.value)
    finally:
        temp_path.unlink()


def test_parse_template_file_missing_template_spec() -> None:
    """Test that missing template_spec block raises error."""
    from tempfile import NamedTemporaryFile

    content = """---
doc_type: session_template_set
domain: running
philosophy_id: daniels
race_types: [5k]
audience: intermediate
phase: build
session_type: threshold
priority: 100
version: "1.0"
---

No template_spec block here
"""

    with NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        temp_path = Path(f.name)

    try:
        with pytest.raises(TemplateParseError) as exc_info:
            parse_template_file(temp_path)
        assert "MISSING_TEMPLATE_SPEC" in str(exc_info.value)
    finally:
        temp_path.unlink()


def test_select_template_for_day_deterministic_rotation() -> None:
    """Test that template selection rotates deterministically."""
    from app.planner.models import SessionTemplate, SessionTemplateSet

    template1 = SessionTemplate(
        template_id="template_1",
        description_key="desc_1",
        kind="kind_1",
        params={},
        constraints={},
        tags=[],
    )
    template2 = SessionTemplate(
        template_id="template_2",
        description_key="desc_2",
        kind="kind_2",
        params={},
        constraints={},
        tags=[],
    )

    template_set = SessionTemplateSet(
        domain="running",
        philosophy_id="daniels",
        phase="build",
        session_type="threshold",
        race_types=["5k"],
        audience="intermediate",
        priority=100,
        version="1.0",
        templates=[template1, template2],
    )

    # Week 1, Day 0 -> (1 * 100 + 0) % 2 = 0 -> template1
    selected = select_template_for_day(template_set, week_index=1, day_index=0)
    assert selected.template_id == "template_1"

    # Week 1, Day 1 -> (1 * 100 + 1) % 2 = 1 -> template2
    selected = select_template_for_day(template_set, week_index=1, day_index=1)
    assert selected.template_id == "template_2"

    # Week 2, Day 0 -> (2 * 100 + 0) % 2 = 0 -> template1
    selected = select_template_for_day(template_set, week_index=2, day_index=0)
    assert selected.template_id == "template_1"

    # Week 1, Day 2 -> (1 * 100 + 2) % 2 = 0 -> template1
    selected = select_template_for_day(template_set, week_index=1, day_index=2)
    assert selected.template_id == "template_1"


def test_select_template_for_day_empty_set() -> None:
    """Test that empty template set raises error."""
    from app.planner.models import SessionTemplateSet

    template_set = SessionTemplateSet(
        domain="running",
        philosophy_id="daniels",
        phase="build",
        session_type="threshold",
        race_types=["5k"],
        audience="intermediate",
        priority=100,
        version="1.0",
        templates=[],
    )

    with pytest.raises(TemplateSelectionError) as exc_info:
        select_template_for_day(template_set, week_index=1, day_index=0)
    assert "no templates" in str(exc_info.value).lower()


def test_select_templates_for_week_build_phase(
    runtime_ctx_daniels: PlanRuntimeContext,
) -> None:
    """Test that templates are selected correctly for build phase."""
    days = [
        DistributedDay(day_index=0, day_type=DayType.EASY, distance=5.0),
        DistributedDay(day_index=1, day_type=DayType.QUALITY, distance=6.0),
        DistributedDay(day_index=2, day_type=DayType.EASY, distance=4.0),
        DistributedDay(day_index=3, day_type=DayType.QUALITY, distance=5.5),
        DistributedDay(day_index=4, day_type=DayType.EASY, distance=4.5),
        DistributedDay(day_index=5, day_type=DayType.EASY, distance=5.0),
        DistributedDay(day_index=6, day_type=DayType.LONG, distance=10.0),
    ]

    # Map day_index to session_type (matching Daniels 5K build structure)
    day_index_to_session_type = {
        0: "easy",
        1: "threshold",
        2: "easy",
        3: "vo2",
        4: "easy",
        5: "easy_plus_strides",
        6: "long",
    }

    planned_sessions = select_templates_for_week(
        context=runtime_ctx_daniels,
        week_index=1,
        phase="build",
        days=days,
        day_index_to_session_type=day_index_to_session_type,
    )

    assert len(planned_sessions) == 7

    # Check that each session has a template
    for session in planned_sessions:
        assert session.template is not None
        assert session.template.template_id is not None
        assert session.template.kind is not None

    # Check threshold day (QUALITY on Tuesday) gets threshold template
    tuesday_session = planned_sessions[1]
    assert tuesday_session.day_type == DayType.QUALITY
    assert tuesday_session.template.kind in ["cruise_intervals", "steady_T_block"]

    # Check VO2 day (QUALITY on Thursday) gets VO2 template
    thursday_session = planned_sessions[3]
    assert thursday_session.day_type == DayType.QUALITY
    assert thursday_session.template.kind in ["vo2_intervals", "vo2_distance_reps"]

    # Check long day gets long template
    sunday_session = planned_sessions[6]
    assert sunday_session.day_type == DayType.LONG
    assert sunday_session.template.kind in ["long_easy", "long_with_strides"]

    # Check easy days get easy templates
    monday_session = planned_sessions[0]
    assert monday_session.day_type == DayType.EASY
    assert monday_session.template.kind in [
        "easy_continuous",
        "easy_progression",
        "easy_with_strides",
        "easy_with_hill_sprints",
    ]


def test_select_templates_for_week_taper_phase(
    runtime_ctx_daniels: PlanRuntimeContext,
) -> None:
    """Test that templates are selected correctly for taper phase."""
    days = [
        DistributedDay(day_index=0, day_type=DayType.EASY, distance=4.0),
        DistributedDay(day_index=1, day_type=DayType.QUALITY, distance=4.5),
        DistributedDay(day_index=2, day_type=DayType.EASY, distance=3.5),
        DistributedDay(day_index=3, day_type=DayType.QUALITY, distance=4.0),
        DistributedDay(day_index=4, day_type=DayType.EASY, distance=3.0),
        DistributedDay(day_index=5, day_type=DayType.EASY, distance=3.5),
        DistributedDay(day_index=6, day_type=DayType.LONG, distance=8.0),
    ]

    # Map day_index to session_type (matching Daniels 5K taper structure)
    day_index_to_session_type = {
        0: "easy",
        1: "threshold",
        2: "easy",
        3: "vo2",
        4: "easy",
        5: "easy_plus_strides",
        6: "long",
    }

    planned_sessions = select_templates_for_week(
        context=runtime_ctx_daniels,
        week_index=1,
        phase="taper",
        days=days,
        day_index_to_session_type=day_index_to_session_type,
    )

    assert len(planned_sessions) == 7

    # Check that taper templates are selected (should have "taper" in tags or be taper-specific)
    for session in planned_sessions:
        assert session.template is not None

    # Check threshold day gets taper threshold template
    tuesday_session = planned_sessions[1]
    assert tuesday_session.day_type == DayType.QUALITY
    assert tuesday_session.template.kind == "cruise_intervals"
    assert "taper" in tuesday_session.template.tags

    # Check VO2 day gets taper VO2 template
    thursday_session = planned_sessions[3]
    assert thursday_session.day_type == DayType.QUALITY
    assert thursday_session.template.kind == "vo2_intervals"
    assert "taper" in thursday_session.template.tags


def test_select_templates_for_week_missing_template_set() -> None:
    """Test that missing template set raises error."""
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=RaceDistance.MARATHON,  # No templates for marathon yet
        target_date="2025-06-15",
    )
    philosophy = PhilosophySelection(
        philosophy_id="daniels",
        domain="running",
        audience="intermediate",
    )
    runtime_ctx = PlanRuntimeContext(plan=ctx, philosophy=philosophy)

    days = [
        DistributedDay(day_index=0, day_type=DayType.EASY, distance=5.0),
    ]

    with pytest.raises(TemplateSelectionError) as exc_info:
        select_templates_for_week(
            context=runtime_ctx,
            week_index=1,
            phase="build",
            days=days,
            day_index_to_session_type={0: "easy"},
        )
    assert "No template set found" in str(exc_info.value)


def test_select_templates_for_week_unknown_day_type() -> None:
    """Test that unknown day type raises error."""
    ctx = PlanContext(
        plan_type=PlanType.RACE,
        intent=TrainingIntent.BUILD,
        weeks=12,
        race_distance=RaceDistance.FIVE_K,
        target_date="2025-06-15",
    )
    philosophy = PhilosophySelection(
        philosophy_id="daniels",
        domain="running",
        audience="intermediate",
    )
    runtime_ctx = PlanRuntimeContext(plan=ctx, philosophy=philosophy)

    # Use REST day type which might not have a session_type mapping
    days = [
        DistributedDay(day_index=0, day_type=DayType.REST, distance=0.0),
    ]

    # This should either work (if REST maps to a session_type) or raise an error
    # For now, we expect it to raise an error if REST doesn't map
    with pytest.raises(TemplateSelectionError):
        select_templates_for_week(
            context=runtime_ctx,
            week_index=1,
            phase="build",
            days=days,
            day_index_to_session_type={0: "rest"},
        )
