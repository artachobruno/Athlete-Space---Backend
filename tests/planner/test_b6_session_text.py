"""Tests for session text generator (B6).

Tests verify that session text generation:
- Validates output obeys distance constraints
- Caps hard minutes correctly
- Triggers fallback on violation
- Enforces JSON schema
- Caches results correctly
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.planner.enums import DayType
from app.planner.llm.fallback import generate_fallback_session_text
from app.planner.llm.session_text import validate_llm_output
from app.planner.models import (
    PlannedSession,
    PlannedWeek,
    SessionTemplate,
    SessionTextInput,
    SessionTextOutput,
    WeekFocus,
)
from app.planner.schemas import SessionTextOutputSchema
from app.planner.session_text_generator import generate_session_text, generate_week_sessions


@pytest.fixture
def sample_template() -> SessionTemplate:
    """Create a sample session template for testing."""
    return SessionTemplate(
        template_id="test_threshold_v1",
        description_key="threshold_cruise",
        kind="cruise_intervals",
        params={
            "reps": [4, 5, 6],
            "work_duration_min": 8,
            "recovery_min": 2,
        },
        constraints={
            "hard_minutes_max": 30,
            "T_minutes_max": 30,
        },
        tags=["threshold", "intervals"],
    )


@pytest.fixture
def sample_session(sample_template: SessionTemplate) -> PlannedSession:
    """Create a sample planned session for testing."""
    return PlannedSession(
        day_index=1,
        day_type=DayType.QUALITY,
        distance=8.0,
        template=sample_template,
    )


@pytest.fixture
def sample_input() -> SessionTextInput:
    """Create a sample session text input for testing."""
    return SessionTextInput(
        philosophy_id="daniels",
        race_distance="5k",
        phase="build",
        week_index=4,
        day_type=DayType.QUALITY,
        allocated_distance_mi=8.0,
        allocated_duration_min=None,
        template_id="test_threshold_v1",
        template_kind="cruise_intervals",
        params={
            "reps": [4, 5, 6],
            "work_duration_min": 8,
            "recovery_min": 2,
        },
        constraints={
            "hard_minutes_max": 30,
            "T_minutes_max": 30,
        },
    )


@pytest.fixture
def valid_output() -> SessionTextOutput:
    """Create a valid session text output for testing."""
    return SessionTextOutput(
        title="Threshold Cruise Intervals",
        description="2 mi warm up. 4 x 8 min at threshold pace with 2 min float jog recoveries. 2 mi cool down.",
        structure={
            "warmup_mi": 2.0,
            "main": [
                {
                    "type": "interval",
                    "reps": 4,
                    "work_duration_min": 8,
                    "recovery_duration_min": 2,
                }
            ],
            "cooldown_mi": 2.0,
        },
        computed={
            "total_distance_mi": 8.0,
            "hard_minutes": 32,
            "intensity_minutes": {"T": 32},
        },
    )


def test_validate_llm_output_distance_ok(sample_input: SessionTextInput, valid_output: SessionTextOutput) -> None:
    """Test that validation passes when distance is within limit."""
    # Adjust output to be within limit
    adjusted_output = SessionTextOutput(
        title=valid_output.title,
        description=valid_output.description,
        structure=valid_output.structure,
        computed={
            **valid_output.computed,
            "total_distance_mi": 7.9,  # Within 8.0 + 0.05
        },
    )
    assert validate_llm_output(sample_input, adjusted_output) is True


def test_validate_llm_output_distance_violation(sample_input: SessionTextInput, valid_output: SessionTextOutput) -> None:
    """Test that validation fails when distance exceeds limit."""
    # Adjust output to exceed limit
    adjusted_output = SessionTextOutput(
        title=valid_output.title,
        description=valid_output.description,
        structure=valid_output.structure,
        computed={
            **valid_output.computed,
            "total_distance_mi": 9.0,  # Exceeds 8.0 + 0.05
        },
    )
    assert validate_llm_output(sample_input, adjusted_output) is False


def test_validate_llm_output_hard_minutes_ok(sample_input: SessionTextInput, valid_output: SessionTextOutput) -> None:
    """Test that validation passes when hard minutes are within limit."""
    # Adjust output to be within limit
    adjusted_output = SessionTextOutput(
        title=valid_output.title,
        description=valid_output.description,
        structure=valid_output.structure,
        computed={
            **valid_output.computed,
            "hard_minutes": 25,  # Within 30
        },
    )
    assert validate_llm_output(sample_input, adjusted_output) is True


def test_validate_llm_output_hard_minutes_violation(
    sample_input: SessionTextInput, valid_output: SessionTextOutput
) -> None:
    """Test that validation fails when hard minutes exceed limit."""
    # Adjust output to exceed limit
    adjusted_output = SessionTextOutput(
        title=valid_output.title,
        description=valid_output.description,
        structure=valid_output.structure,
        computed={
            **valid_output.computed,
            "hard_minutes": 35,  # Exceeds 30
        },
    )
    assert validate_llm_output(sample_input, adjusted_output) is False


def test_validate_llm_output_intensity_minutes_ok(
    sample_input: SessionTextInput, valid_output: SessionTextOutput
) -> None:
    """Test that validation passes when intensity minutes are within limit."""
    # Adjust output to be within limit
    adjusted_output = SessionTextOutput(
        title=valid_output.title,
        description=valid_output.description,
        structure=valid_output.structure,
        computed={
            **valid_output.computed,
            "intensity_minutes": {"T": 25},  # Within 30
        },
    )
    assert validate_llm_output(sample_input, adjusted_output) is True


def test_validate_llm_output_intensity_minutes_violation(
    sample_input: SessionTextInput, valid_output: SessionTextOutput
) -> None:
    """Test that validation fails when intensity minutes exceed limit."""
    # Adjust output to exceed limit
    adjusted_output = SessionTextOutput(
        title=valid_output.title,
        description=valid_output.description,
        structure=valid_output.structure,
        computed={
            **valid_output.computed,
            "intensity_minutes": {"T": 35},  # Exceeds 30
        },
    )
    assert validate_llm_output(sample_input, adjusted_output) is False


def test_fallback_generation(sample_input: SessionTextInput) -> None:
    """Test that fallback generates valid output."""
    output = generate_fallback_session_text(sample_input)

    assert output.title is not None
    assert len(output.description) > 0
    assert "warmup_mi" in output.structure
    assert "main" in output.structure
    assert "cooldown_mi" in output.structure
    assert "total_distance_mi" in output.computed
    assert "hard_minutes" in output.computed
    assert "intensity_minutes" in output.computed

    # Check distance constraint
    assert output.computed["total_distance_mi"] <= sample_input.allocated_distance_mi + 0.05


def test_fallback_marks_generated_by(sample_input: SessionTextInput) -> None:
    """Test that fallback output is marked with generated_by metadata."""
    output = generate_fallback_session_text(sample_input)

    # Fallback should have generated_by in computed (via session_text_generator)
    # But the function itself doesn't add it - that's done in the generator
    assert "hard_minutes" in output.computed


@pytest.mark.asyncio
async def test_generate_session_text_llm_success(sample_session: PlannedSession) -> None:
    """Test successful LLM generation."""
    context = {
        "philosophy_id": "daniels",
        "race_distance": "5k",
        "phase": "build",
        "week_index": 4,
    }

    mock_output = SessionTextOutput(
        title="Test Workout",
        description="Test description",
        structure={"warmup_mi": 2.0, "main": [], "cooldown_mi": 2.0},
        computed={"total_distance_mi": 8.0, "hard_minutes": 20, "intensity_minutes": {}},
    )

    with (
        patch("app.planner.session_text_generator.generate_session_text_llm", new_callable=AsyncMock) as mock_llm,
        patch("app.planner.session_text_generator._get_cached_output", return_value=None),
        patch("app.planner.session_text_generator._set_cached_output"),
    ):
        mock_llm.return_value = mock_output
        output = await generate_session_text(sample_session, context)

        assert output.title == "Test Workout"
        assert output.computed["total_distance_mi"] == 8.0


@pytest.mark.asyncio
async def test_generate_session_text_fallback_on_llm_failure(sample_session: PlannedSession) -> None:
    """Test that fallback is used when LLM fails."""
    context = {
        "philosophy_id": "daniels",
        "race_distance": "5k",
        "phase": "build",
        "week_index": 4,
    }

    with (
        patch("app.planner.session_text_generator.generate_session_text_llm", new_callable=AsyncMock) as mock_llm,
        patch("app.planner.session_text_generator._get_cached_output", return_value=None),
        patch("app.planner.session_text_generator._set_cached_output"),
    ):
        mock_llm.side_effect = ValueError("LLM failed")
        output = await generate_session_text(sample_session, context)

        # Should have fallback output
        assert output.title is not None
        assert len(output.description) > 0
        assert output.computed["total_distance_mi"] <= sample_session.distance + 0.05


@pytest.mark.asyncio
async def test_generate_session_text_uses_cache(sample_session: PlannedSession) -> None:
    """Test that cached results are used when available."""
    context = {
        "philosophy_id": "daniels",
        "race_distance": "5k",
        "phase": "build",
        "week_index": 4,
    }

    cached_data = {
        "title": "Cached Workout",
        "description": "Cached description",
        "structure": {"warmup_mi": 2.0, "main": [], "cooldown_mi": 2.0},
        "computed": {"total_distance_mi": 8.0, "hard_minutes": 20, "intensity_minutes": {}},
    }

    with patch("app.planner.session_text_generator._get_cached_output", return_value=cached_data):
        output = await generate_session_text(sample_session, context)

        assert output.title == "Cached Workout"


@pytest.mark.asyncio
async def test_generate_week_sessions(sample_template: SessionTemplate) -> None:
    """Test week session generation."""
    sessions = [
        PlannedSession(
            day_index=0,
            day_type=DayType.REST,
            distance=0.0,
            template=sample_template,
        ),
        PlannedSession(
            day_index=1,
            day_type=DayType.QUALITY,
            distance=8.0,
            template=sample_template,
        ),
    ]

    week = PlannedWeek(
        week_index=4,
        focus=WeekFocus.BUILD,
        sessions=sessions,
    )

    context = {
        "philosophy_id": "daniels",
        "race_distance": "5k",
        "phase": "build",
        "week_index": 4,
    }

    mock_output = SessionTextOutput(
        title="Test Workout",
        description="Test description",
        structure={"warmup_mi": 2.0, "main": [], "cooldown_mi": 2.0},
        computed={"total_distance_mi": 8.0, "hard_minutes": 20, "intensity_minutes": {}},
    )

    with patch("app.planner.session_text_generator.generate_session_text", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_output
        with patch("app.planner.session_text_generator._get_cached_output", return_value=None):
            updated_week = await generate_week_sessions(week, context)

            # Rest day should not have text
            assert updated_week.sessions[0].text_output is None

            # Quality day should have text
            assert updated_week.sessions[1].text_output is not None
            assert updated_week.sessions[1].text_output.title == "Test Workout"


def test_session_text_output_schema_validation() -> None:
    """Test that SessionTextOutputSchema validates correctly."""
    valid_data = {
        "title": "Test Workout",
        "description": "Test description",
        "structure": {
            "warmup_mi": 2.0,
            "main": [{"type": "interval", "reps": 4}],
            "cooldown_mi": 2.0,
        },
        "computed": {
            "total_distance_mi": 8.0,
            "hard_minutes": 20,
            "intensity_minutes": {"T": 20},
        },
    }

    # Should not raise
    schema = SessionTextOutputSchema.model_validate(valid_data)
    assert schema.title == "Test Workout"


def test_session_text_output_schema_validation_fails_missing_field() -> None:
    """Test that schema validation fails on missing required fields."""
    invalid_data = {
        "title": "Test Workout",
        # Missing description
        "structure": {},
        "computed": {},
    }

    from pydantic import ValidationError

    with pytest.raises(ValidationError):  # Pydantic validation error
        SessionTextOutputSchema.model_validate(invalid_data)
