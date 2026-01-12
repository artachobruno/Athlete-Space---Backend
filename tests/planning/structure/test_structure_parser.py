"""Tests for structure specification parser.

Tests that all structure files parse correctly.
"""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.planning.structure.spec_parser import StructureParseError, parse_structure_file


def get_all_structure_files() -> list[Path]:
    """Get all structure markdown files."""
    structures_dir = _project_root / "data" / "rag" / "planning" / "structures"
    return list(structures_dir.rglob("*.md"))


@pytest.mark.parametrize("structure_file", get_all_structure_files())
def test_all_structures_parse(structure_file: Path) -> None:
    """Test that every structure file parses correctly."""
    spec = parse_structure_file(structure_file)

    # Verify basic structure
    assert spec.metadata.id is not None
    assert spec.metadata.philosophy_id is not None
    assert len(spec.metadata.race_types) > 0
    assert spec.metadata.audience is not None
    assert spec.metadata.phase is not None
    assert spec.metadata.days_to_race_min >= 0
    assert spec.metadata.days_to_race_max >= spec.metadata.days_to_race_min
    assert spec.metadata.priority > 0

    # Verify week pattern exists
    assert len(spec.week_pattern) > 0

    # Verify session groups exist
    assert len(spec.session_groups) > 0

    # Verify rules exist
    assert len(spec.rules) > 0


def test_parse_missing_frontmatter(tmp_path: Path) -> None:
    """Test that missing frontmatter raises error."""
    test_file = tmp_path / "test.md"
    test_file.write_text("No frontmatter here\n")

    with pytest.raises(StructureParseError, match="MISSING_FRONTMATTER"):
        parse_structure_file(test_file)


def test_parse_missing_structure_spec(tmp_path: Path) -> None:
    """Test that missing structure_spec block raises error."""
    test_file = tmp_path / "test.md"
    test_file.write_text(
        """---
doc_type: plan_structure
philosophy_id: test
id: test_structure
race_types: [5k]
audience: intermediate
phase: build
days_to_race_min: 0
days_to_race_max: 100
priority: 100
---

No structure_spec block here.
"""
    )

    with pytest.raises(StructureParseError, match="MISSING_STRUCTURE_SPEC"):
        parse_structure_file(test_file)


def test_parse_multiple_structure_spec(tmp_path: Path) -> None:
    """Test that multiple structure_spec blocks raise error."""
    test_file = tmp_path / "test.md"
    test_file.write_text(
        """---
doc_type: plan_structure
philosophy_id: test
id: test_structure
race_types: [5k]
audience: intermediate
phase: build
days_to_race_min: 0
days_to_race_max: 100
priority: 100
---

```structure_spec
week_pattern:
  mon: easy
```

```structure_spec
week_pattern:
  mon: easy
```
"""
    )

    with pytest.raises(StructureParseError, match="MULTIPLE_STRUCTURE_SPEC"):
        parse_structure_file(test_file)


def test_parse_invalid_yaml(tmp_path: Path) -> None:
    """Test that invalid YAML raises error."""
    test_file = tmp_path / "test.md"
    test_file.write_text(
        """---
doc_type: plan_structure
philosophy_id: test
id: test_structure
race_types: [5k]
audience: intermediate
phase: build
days_to_race_min: 0
days_to_race_max: 100
priority: 100
---

```structure_spec
week_pattern:
  mon: easy
  invalid: yaml: : : :
```
"""
    )

    with pytest.raises(StructureParseError, match="INVALID_STRUCTURE_SPEC_YAML"):
        parse_structure_file(test_file)
