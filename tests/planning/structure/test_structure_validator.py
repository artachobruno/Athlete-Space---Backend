"""Tests for structure specification validator.

Tests validation rules:
- Week shape (7 days, valid names)
- Hard day logic (count, no consecutive)
- Long run enforcement (required_count matches actual)
- Phase overrides (taper rules)
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.planning.structure.spec_parser import parse_structure_file
from app.planning.structure.types import StructureMetadata, StructureSpec
from app.planning.structure.validator import (
    StructureValidationError,
    validate_structure,
)


def get_all_structure_files() -> list[Path]:
    """Get all structure markdown files."""
    structures_dir = _project_root / "data" / "rag" / "planning" / "structures"
    return list(structures_dir.rglob("*.md"))


@pytest.mark.parametrize("structure_file", get_all_structure_files())
def test_all_structures_validate(structure_file: Path) -> None:
    """Test that every structure file validates correctly.

    Note: This test will fail if any structure file has validation errors.
    This is intentional - structure files must be valid.
    """
    spec = parse_structure_file(structure_file)
    validated = validate_structure(spec)

    # Validation should return the same object (unchanged)
    assert validated is spec


def test_validate_invalid_week_shape() -> None:
    """Test that invalid week shape (wrong number of days) fails."""
    metadata = StructureMetadata(
        id="test",
        philosophy_id="test",
        race_types=["5k"],
        audience="intermediate",
        phase="build",
        days_to_race_min=0,
        days_to_race_max=100,
        priority=100,
    )

    # Only 6 days (missing one)
    spec = StructureSpec(
        metadata=metadata,
        week_pattern={
            "mon": "easy",
            "tue": "easy",
            "wed": "easy",
            "thu": "easy",
            "fri": "easy",
            "sat": "easy",
        },
        rules={},
        session_groups={"easy": ["easy"]},
        guards={},
        notes={},
    )

    with pytest.raises(StructureValidationError, match="INVALID_WEEK_SHAPE"):
        validate_structure(spec)


def test_validate_invalid_day_name() -> None:
    """Test that invalid day name fails."""
    metadata = StructureMetadata(
        id="test",
        philosophy_id="test",
        race_types=["5k"],
        audience="intermediate",
        phase="build",
        days_to_race_min=0,
        days_to_race_max=100,
        priority=100,
    )

    spec = StructureSpec(
        metadata=metadata,
        week_pattern={
            "monday": "easy",  # Invalid (should be "mon")
            "tue": "easy",
            "wed": "easy",
            "thu": "easy",
            "fri": "easy",
            "sat": "easy",
            "sun": "easy",
        },
        rules={},
        session_groups={"easy": ["easy"]},
        guards={},
        notes={},
    )

    with pytest.raises(StructureValidationError, match="INVALID_DAY_NAMES"):
        validate_structure(spec)


def test_validate_too_many_hard_days() -> None:
    """Test that too many hard days fails."""
    metadata = StructureMetadata(
        id="test",
        philosophy_id="test",
        race_types=["5k"],
        audience="intermediate",
        phase="build",
        days_to_race_min=0,
        days_to_race_max=100,
        priority=100,
    )

    spec = StructureSpec(
        metadata=metadata,
        week_pattern={
            "mon": "easy",
            "tue": "hard",  # Hard day 1
            "wed": "easy",
            "thu": "hard",  # Hard day 2
            "fri": "easy",
            "sat": "easy",
            "sun": "easy",
        },
        rules={"hard_days_max": 1},  # Only 1 allowed
        session_groups={"hard": ["hard"], "easy": ["easy"]},
        guards={},
        notes={},
    )

    with pytest.raises(StructureValidationError, match="TOO_MANY_HARD_DAYS"):
        validate_structure(spec)


def test_validate_consecutive_hard_days() -> None:
    """Test that consecutive hard days fails when no_consecutive_hard_days is true."""
    metadata = StructureMetadata(
        id="test",
        philosophy_id="test",
        race_types=["5k"],
        audience="intermediate",
        phase="build",
        days_to_race_min=0,
        days_to_race_max=100,
        priority=100,
    )

    spec = StructureSpec(
        metadata=metadata,
        week_pattern={
            "mon": "easy",
            "tue": "hard",  # Hard day 1
            "wed": "hard",  # Hard day 2 (consecutive!)
            "thu": "easy",
            "fri": "easy",
            "sat": "easy",
            "sun": "easy",
        },
        rules={
            "hard_days_max": 2,
            "no_consecutive_hard_days": True,  # Consecutive not allowed
        },
        session_groups={"hard": ["hard"], "easy": ["easy"]},
        guards={},
        notes={},
    )

    with pytest.raises(StructureValidationError, match="CONSECUTIVE_HARD_DAYS"):
        validate_structure(spec)


def test_validate_long_run_count_mismatch() -> None:
    """Test that long run count mismatch fails."""
    metadata = StructureMetadata(
        id="test",
        philosophy_id="test",
        race_types=["5k"],
        audience="intermediate",
        phase="build",
        days_to_race_min=0,
        days_to_race_max=100,
        priority=100,
    )

    spec = StructureSpec(
        metadata=metadata,
        week_pattern={
            "mon": "easy",
            "tue": "easy",
            "wed": "easy",
            "thu": "easy",
            "fri": "easy",
            "sat": "long",  # 1 long run
            "sun": "easy",
        },
        rules={"long_run": {"required_count": 2}},  # But 2 required!
        session_groups={"long": ["long"], "easy": ["easy"]},
        guards={},
        notes={},
    )

    with pytest.raises(StructureValidationError, match="LONG_RUN_COUNT_MISMATCH"):
        validate_structure(spec)


def test_validate_taper_too_many_long_runs() -> None:
    """Test that taper phase with >1 long runs fails."""
    metadata = StructureMetadata(
        id="test",
        philosophy_id="test",
        race_types=["5k"],
        audience="intermediate",
        phase="taper",  # Taper phase
        days_to_race_min=0,
        days_to_race_max=28,
        priority=200,
    )

    spec = StructureSpec(
        metadata=metadata,
        week_pattern={
            "mon": "easy",
            "tue": "easy",
            "wed": "easy",
            "thu": "easy",
            "fri": "easy",
            "sat": "long",  # Long run 1
            "sun": "long",  # Long run 2
        },
        rules={"long_run": {"required_count": 2}},  # 2 long runs
        session_groups={"long": ["long"], "easy": ["easy"]},
        guards={},
        notes={},
    )

    with pytest.raises(StructureValidationError, match="TAPER_LONG_RUN_TOO_MANY"):
        validate_structure(spec)


def test_validate_taper_too_many_hard_days() -> None:
    """Test that taper phase with >1 hard days fails."""
    metadata = StructureMetadata(
        id="test",
        philosophy_id="test",
        race_types=["5k"],
        audience="intermediate",
        phase="taper",  # Taper phase
        days_to_race_min=0,
        days_to_race_max=28,
        priority=200,
    )

    spec = StructureSpec(
        metadata=metadata,
        week_pattern={
            "mon": "easy",
            "tue": "hard",  # Hard day 1
            "wed": "easy",
            "thu": "hard",  # Hard day 2
            "fri": "easy",
            "sat": "easy",
            "sun": "easy",
        },
        rules={"hard_days_max": 2},  # 2 hard days
        session_groups={"hard": ["hard"], "easy": ["easy"]},
        guards={},
        notes={},
    )

    with pytest.raises(StructureValidationError, match="TAPER_HARD_DAYS_TOO_MANY"):
        validate_structure(spec)
