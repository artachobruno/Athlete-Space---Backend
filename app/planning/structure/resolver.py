"""Structure specification resolver.

This module resolves the correct structure specification based on:
- philosophy_id
- race_type
- audience
- phase
- days_to_race

Resolution algorithm (ORDER MATTERS):
1. Load all structure files
2. Filter by philosophy_id, race_type, audience, phase
3. Filter by days_to_race range
4. Sort by priority DESC
5. Assert exactly one winner
"""

from pathlib import Path

from app.planning.context import PlanningContext
from app.planning.structure.spec_parser import StructureParseError, parse_structure_file
from app.planning.structure.types import StructureSpec
from app.planning.structure.validator import StructureValidationError, validate_structure


class StructureResolutionError(RuntimeError):
    """Raised when structure resolution fails.

    Attributes:
        code: Error code
        message: Error message
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _get_structures_dir() -> Path:
    """Get path to structures directory.

    Returns:
        Path to data/rag/planning/structures directory
    """
    # Resolve from project root (assuming this file is in app/planning/structure/)
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "data" / "rag" / "planning" / "structures"


def _load_all_structures() -> list[StructureSpec]:
    """Load all structure files from the structures directory.

    Returns:
        List of parsed and validated StructureSpec instances

    Raises:
        StructureResolutionError: If loading fails
    """
    structures_dir = _get_structures_dir()

    if not structures_dir.exists():
        raise StructureResolutionError(
            "STRUCTURES_DIR_NOT_FOUND",
            f"Structures directory not found: {structures_dir}",
        )

    loaded_structures: list[StructureSpec] = []

    # Recursively find all .md files
    for md_file in structures_dir.rglob("*.md"):
        try:
            spec = parse_structure_file(md_file)
            # Validate immediately after parsing
            validate_structure(spec)
            loaded_structures.append(spec)
        except (StructureParseError, StructureValidationError) as e:
            # Log but continue - allow partial loading for development
            # In production, these should be caught earlier
            raise StructureResolutionError(
                "STRUCTURE_LOAD_ERROR",
                f"Failed to load/validate structure file {md_file}: {e}",
            ) from e
        except Exception as e:
            raise StructureResolutionError(
                "STRUCTURE_LOAD_ERROR",
                f"Unexpected error loading structure file {md_file}: {e}",
            ) from e

    return loaded_structures


def resolve_structure(context: PlanningContext) -> StructureSpec:
    """Resolve structure specification from planning context.

    Resolution algorithm:
    1. Load all structure files
    2. Filter by philosophy_id, race_type ∈ race_types, audience, phase
    3. Filter by days_to_race_min <= days_to_race <= days_to_race_max
    4. Sort by priority DESC
    5. Assert exactly one winner

    Args:
        context: Planning context with all resolution parameters

    Returns:
        Resolved StructureSpec

    Raises:
        StructureResolutionError: If resolution fails (0 matches or >1 matches)
    """
    # Step 1: Load all structures
    all_structures = _load_all_structures()

    # Step 2: Filter by exact matches
    candidates = [
        s
        for s in all_structures
        if s.metadata.philosophy_id == context.philosophy_id
        and context.race_type in s.metadata.race_types
        and s.metadata.audience == context.audience
        and s.metadata.phase == context.phase
    ]

    # Step 3: Filter by days_to_race range
    candidates = [
        s
        for s in candidates
        if s.metadata.days_to_race_min <= context.days_to_race <= s.metadata.days_to_race_max
    ]

    # Step 4: Sort by priority DESC
    candidates.sort(key=lambda s: s.metadata.priority, reverse=True)

    # Step 5: Assert exactly one winner
    if len(candidates) == 0:
        raise StructureResolutionError(
            "NO_MATCHING_STRUCTURE",
            (
                f"No structure found for philosophy_id={context.philosophy_id}, "
                f"race_type={context.race_type}, audience={context.audience}, "
                f"phase={context.phase}, days_to_race={context.days_to_race}"
            ),
        )

    if len(candidates) > 1:
        # Multiple matches - this is a design error
        candidate_ids = [c.metadata.id for c in candidates]
        raise StructureResolutionError(
            "AMBIGUOUS_STRUCTURE",
            (
                f"Multiple structures match: {candidate_ids}. "
                f"Criteria: philosophy_id={context.philosophy_id}, "
                f"race_type={context.race_type}, audience={context.audience}, "
                f"phase={context.phase}, days_to_race={context.days_to_race}. "
                f"All have priority={candidates[0].metadata.priority}"
            ),
        )

    # Return the single winner (already validated during loading)
    return candidates[0]
