"""Phase 4 Template Selection Tests.

Tests for bounded LLM template selection with deterministic fallback.
All tests ensure invariants are maintained.
"""

import pytest

from app.planning.errors import PlanningInvariantError
from app.planning.library.session_template import SessionTemplate
from app.planning.llm.schemas import DayTemplateCandidates, WeekSelectionInput, WeekTemplateSelection
from app.planning.llm.validate import validate_selection
from app.planning.selection.fallback import fallback_select


def create_test_template(
    template_id: str,
    session_type: str,
    intensity_level: str,
    min_duration: int,
    max_duration: int,
    race_types: list[str] | None = None,
    phase_tags: list[str] | None = None,
) -> SessionTemplate:
    """Helper to create test session templates."""
    from app.planning.library.session_template import SessionType

    # Convert string to SessionType literal
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
        race_types=race_types or ["5k", "10k", "half", "marathon"],
        phase_tags=phase_tags or ["base", "build", "peak", "taper"],
        min_duration_min=min_duration,
        max_duration_min=max_duration,
        tags=[],
    )


def test_fallback_always_produces_valid_output():
    """Test that fallback always produces valid selection."""
    candidates = [
        DayTemplateCandidates(
            day="mon",
            role="easy",
            duration_minutes=45,
            candidate_template_ids=["easy_1", "easy_2"],
        ),
        DayTemplateCandidates(
            day="tue",
            role="hard",
            duration_minutes=60,
            candidate_template_ids=["hard_1"],
        ),
    ]

    selection = fallback_select(week_index=0, candidates=candidates)

    assert selection.week_index == 0
    assert len(selection.selections) == 2
    assert selection.selections["mon"] in ["easy_1", "easy_2"]
    assert selection.selections["tue"] == "hard_1"


def test_fallback_chooses_first_candidate():
    """Test that fallback chooses first candidate (lowest risk)."""
    candidates = [
        DayTemplateCandidates(
            day="sat",
            role="long",
            duration_minutes=90,
            candidate_template_ids=["long_1", "long_2", "long_3"],
        ),
    ]

    selection = fallback_select(week_index=0, candidates=candidates)

    assert selection.selections["sat"] == "long_1"  # First candidate


def test_validate_selection_all_days_present():
    """Test validation rejects selection with missing days."""
    candidates = [
        DayTemplateCandidates(
            day="mon",
            role="easy",
            duration_minutes=45,
            candidate_template_ids=["easy_1"],
        ),
        DayTemplateCandidates(
            day="tue",
            role="hard",
            duration_minutes=60,
            candidate_template_ids=["hard_1"],
        ),
    ]

    selection = WeekTemplateSelection(
        week_index=0,
        selections={"mon": "easy_1"},  # Missing tue
    )

    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_selection(selection, candidates)

    assert exc_info.value.code == "INVALID_TEMPLATE_SELECTION"
    assert "Missing selections" in str(exc_info.value.details)


def test_validate_selection_invalid_template_id():
    """Test validation rejects selection with invalid template ID."""
    candidates = [
        DayTemplateCandidates(
            day="mon",
            role="easy",
            duration_minutes=45,
            candidate_template_ids=["easy_1", "easy_2"],
        ),
    ]

    selection = WeekTemplateSelection(
        week_index=0,
        selections={"mon": "invalid_id"},  # Not in candidates
    )

    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_selection(selection, candidates)

    assert exc_info.value.code == "INVALID_TEMPLATE_SELECTION"
    assert "Invalid template ID" in str(exc_info.value.details)


def test_validate_selection_no_extra_days():
    """Test validation rejects selection with extra days."""
    candidates = [
        DayTemplateCandidates(
            day="mon",
            role="easy",
            duration_minutes=45,
            candidate_template_ids=["easy_1"],
        ),
    ]

    selection = WeekTemplateSelection(
        week_index=0,
        selections={"mon": "easy_1", "wed": "easy_1"},  # Extra day
    )

    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_selection(selection, candidates)

    assert exc_info.value.code == "INVALID_TEMPLATE_SELECTION"
    assert "Extra selections" in str(exc_info.value.details)


def test_validate_selection_valid_selection_passes():
    """Test that valid selection passes validation."""
    candidates = [
        DayTemplateCandidates(
            day="mon",
            role="easy",
            duration_minutes=45,
            candidate_template_ids=["easy_1", "easy_2"],
        ),
        DayTemplateCandidates(
            day="sat",
            role="long",
            duration_minutes=90,
            candidate_template_ids=["long_1"],
        ),
    ]

    selection = WeekTemplateSelection(
        week_index=0,
        selections={"mon": "easy_1", "sat": "long_1"},
    )

    # Should not raise
    validate_selection(selection, candidates)


def test_candidate_retriever_filters_by_duration():
    """Test that candidate retriever filters templates by duration."""
    from app.planning.library.philosophy import TrainingPhilosophy
    from app.planning.selection.candidate_retriever import get_candidates

    templates = [
        create_test_template("easy_30", "easy", "easy", 30, 45),
        create_test_template("easy_45", "easy", "easy", 40, 60),
        create_test_template("easy_60", "easy", "easy", 50, 90),
    ]

    philosophy = TrainingPhilosophy(
        id="test",
        name="Test",
        applicable_race_types=["5k"],
        max_hard_days_per_week=2,
        require_long_run=True,
        long_run_ratio_min=0.2,
        long_run_ratio_max=0.3,
        taper_weeks=2,
        taper_volume_reduction_pct=30.0,
        preferred_session_tags={},
    )

    candidates = get_candidates(
        day_role="easy",
        duration_min=45,
        philosophy=philosophy,
        race_type="5k",
        phase="base",
        all_templates=templates,
    )

    # Should only return templates that include 45 in their range
    candidate_ids = {t.id for t in candidates}
    assert "easy_30" in candidate_ids  # 30-45 includes 45
    assert "easy_45" in candidate_ids  # 40-60 includes 45
    assert "easy_60" not in candidate_ids  # 50-90 doesn't include 45


def test_fallback_idempotency():
    """Test that fallback produces same output for same input."""
    candidates = [
        DayTemplateCandidates(
            day="mon",
            role="easy",
            duration_minutes=45,
            candidate_template_ids=["easy_1", "easy_2"],
        ),
    ]

    selection1 = fallback_select(week_index=0, candidates=candidates)
    selection2 = fallback_select(week_index=0, candidates=candidates)

    assert selection1.selections == selection2.selections
    assert selection1.week_index == selection2.week_index


def test_validate_selection_adjacent_hard_days():
    """Test validation rejects adjacent hard days."""
    candidates = [
        DayTemplateCandidates(
            day="tue",
            role="hard",
            duration_minutes=60,
            candidate_template_ids=["hard_1"],
        ),
        DayTemplateCandidates(
            day="wed",
            role="hard",
            duration_minutes=60,
            candidate_template_ids=["hard_2"],
        ),
    ]

    selection = WeekTemplateSelection(
        week_index=0,
        selections={"tue": "hard_1", "wed": "hard_2"},
    )

    with pytest.raises(PlanningInvariantError) as exc_info:
        validate_selection(selection, candidates)

    assert exc_info.value.code == "INVALID_TEMPLATE_SELECTION"
    assert "Adjacent hard days" in str(exc_info.value.details)
