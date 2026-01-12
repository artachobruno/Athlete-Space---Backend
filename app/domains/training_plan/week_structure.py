"""Week structure loader for hierarchical planner.

This module loads week structures from RAG documents (plan_structure markdown files).
It performs deterministic filtering and selection based on:
- PlanRuntimeContext (race_distance, philosophy)
- MacroWeek.focus (phase)
- Athlete audience (from philosophy)
- Days to race

No LLM. No math. No inference. Pure structure selection.

IMPORTANT: Only searches within the selected philosophy's namespace to prevent
cross-philosophy structure leaks.
"""

from pathlib import Path

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.domains.training_plan.enums import DayType, WeekFocus
from app.domains.training_plan.errors import InvalidSkeletonError
from app.domains.training_plan.models import DaySkeleton, MacroWeek, PlanRuntimeContext, WeekStructure
from app.planning.structure.spec_parser import StructureParseError, parse_structure_file
from app.planning.structure.types import StructureSpec
from app.planning.structure.validator import StructureValidationError, validate_structure

# Day name to index mapping (0 = Monday, 6 = Sunday)
DAY_NAME_TO_INDEX: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

# Session type to DayType mapping
SESSION_TYPE_TO_DAY_TYPE: dict[str, DayType] = {
    "easy": DayType.EASY,
    "easy_plus_strides": DayType.EASY,
    "threshold": DayType.QUALITY,
    "vo2": DayType.QUALITY,
    "long": DayType.LONG,
    "rest": DayType.REST,
    "race": DayType.RACE,
    "cross": DayType.CROSS,
}


def get_structures_dir() -> Path:
    """Get path to structures directory.

    Returns:
        Path to data/rag/planning/structures directory
    """
    project_root = Path(__file__).resolve().parents[3]
    return project_root / "data" / "rag" / "planning" / "structures"


def load_structures_from_philosophy(
    domain: str,
    philosophy_id: str,
) -> list[StructureSpec]:
    """Load structure files only from a specific philosophy namespace.

    This ensures no cross-philosophy structure leaks. Only searches within:
    data/rag/planning/structures/<domain>/<philosophy_id>/

    Args:
        domain: Domain type ("running" | "ultra")
        philosophy_id: Philosophy identifier (e.g., "daniels", "pfitzinger")

    Returns:
        List of parsed and validated StructureSpec instances from philosophy namespace

    Raises:
        InvalidSkeletonError: If loading fails or namespace doesn't exist
    """
    structures_dir = get_structures_dir()
    philosophy_dir = structures_dir / domain / philosophy_id

    if not philosophy_dir.exists():
        raise InvalidSkeletonError(
            f"Philosophy namespace not found: {philosophy_dir}. "
            f"Domain={domain}, philosophy_id={philosophy_id}"
        )

    loaded_structures: list[StructureSpec] = []

    for md_file in philosophy_dir.rglob("*.md"):
        try:
            spec = parse_structure_file(md_file)
            validate_structure(spec)
            loaded_structures.append(spec)
        except (StructureParseError, StructureValidationError) as e:
            logger.warning(f"Failed to load structure file {md_file}: {e}")
            raise InvalidSkeletonError(f"Failed to load structure file {md_file}: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error loading structure file {md_file}: {e}")
            raise InvalidSkeletonError(f"Unexpected error loading structure file {md_file}: {e}") from e

    return loaded_structures


def _load_all_structures() -> list[StructureSpec]:
    """Load all structure files from the structures directory.

    Returns:
        List of parsed and validated StructureSpec instances

    Raises:
        InvalidSkeletonError: If loading fails
    """
    structures_dir = get_structures_dir()

    if not structures_dir.exists():
        raise InvalidSkeletonError(f"Structures directory not found: {structures_dir}")

    loaded_structures: list[StructureSpec] = []

    for md_file in structures_dir.rglob("*.md"):
        try:
            spec = parse_structure_file(md_file)
            validate_structure(spec)
            loaded_structures.append(spec)
        except (StructureParseError, StructureValidationError) as e:
            logger.warning(f"Failed to load structure file {md_file}: {e}")
            raise InvalidSkeletonError(f"Failed to load structure file {md_file}: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error loading structure file {md_file}: {e}")
            raise InvalidSkeletonError(f"Unexpected error loading structure file {md_file}: {e}") from e

    return loaded_structures


def _map_session_type_to_day_type(session_type: str) -> DayType:
    """Map RAG session type to DayType enum.

    Args:
        session_type: Session type from RAG (e.g., "threshold", "vo2", "easy")

    Returns:
        Corresponding DayType enum value

    Raises:
        InvalidSkeletonError: If session type is not recognized
    """
    day_type = SESSION_TYPE_TO_DAY_TYPE.get(session_type.lower())
    if day_type is None:
        raise InvalidSkeletonError(f"Unknown session type: {session_type}")
    return day_type


def load_week_structure(
    ctx: PlanRuntimeContext,
    week: MacroWeek,
    _athlete_state: AthleteState,
    days_to_race: int,
) -> WeekStructure:
    """Load week structure from RAG based on deterministic filtering.

    IMPORTANT: Only searches within the selected philosophy's namespace to prevent
    cross-philosophy structure leaks.

    Selection algorithm (MANDATED ORDER):
    1. Restrict to philosophy namespace (<domain>/<philosophy_id>/)
    2. doc_type == plan_structure (enforced by parser)
    3. philosophy_id == ctx.philosophy.philosophy_id (namespace guarantee)
    4. race_distance ∈ race_types
    5. audience == ctx.philosophy.audience
    6. phase == week.focus
    7. days_to_race_min ≤ days_to_race ≤ days_to_race_max
    8. Select highest priority
    9. Tie-break by newest version (if available)

    Args:
        ctx: Runtime context with plan and selected philosophy
        week: Macro week with focus
        _athlete_state: Athlete state (for validation, not used in filtering)
        days_to_race: Days until race

    Returns:
        WeekStructure with days, rules, session_groups, guards

    Raises:
        InvalidSkeletonError: If no matching structure is found
    """
    if ctx.plan.race_distance is None:
        raise InvalidSkeletonError("Race distance is required for structure selection")

    # Load structures only from philosophy namespace (prevents cross-philosophy leaks)
    all_structures = load_structures_from_philosophy(
        domain=ctx.philosophy.domain,
        philosophy_id=ctx.philosophy.philosophy_id,
    )

    # Filter by exact matches (steps 3-6)
    matches = [
        spec
        for spec in all_structures
        if spec.metadata.philosophy_id == ctx.philosophy.philosophy_id
        and spec.metadata.race_types
        and ctx.plan.race_distance.value in spec.metadata.race_types
        and spec.metadata.audience == ctx.philosophy.audience
        and spec.metadata.phase == week.focus.value
    ]

    # Filter by days_to_race range (step 5)
    matches = [
        spec
        for spec in matches
        if spec.metadata.days_to_race_min <= days_to_race <= spec.metadata.days_to_race_max
    ]

    if not matches:
        raise InvalidSkeletonError(
            f"No plan_structure found for philosophy={ctx.philosophy.philosophy_id}, "
            f"focus={week.focus.value}, race={ctx.plan.race_distance.value}, "
            f"audience={ctx.philosophy.audience}, days_to_race={days_to_race}"
        )

    # Sort by priority DESC (step 6)
    # Note: version tie-breaking not yet implemented (StructureMetadata doesn't have version)
    matches.sort(key=lambda s: s.metadata.priority, reverse=True)

    best = matches[0]

    # Convert week_pattern to DaySkeleton list and build day_index_to_session_type mapping
    days: list[DaySkeleton] = []
    day_index_to_session_type: dict[int, str] = {}
    for day_name, session_type in best.week_pattern.items():
        day_index = DAY_NAME_TO_INDEX.get(day_name.lower())
        if day_index is None:
            raise InvalidSkeletonError(f"Unknown day name: {day_name}")

        day_type = _map_session_type_to_day_type(session_type)
        days.append(DaySkeleton(day_index=day_index, day_type=day_type))
        day_index_to_session_type[day_index] = session_type

    # Sort days by day_index to ensure consistent ordering
    days.sort(key=lambda d: d.day_index)

    logger.debug(
        "Loaded week structure",
        structure_id=best.metadata.id,
        philosophy_id=best.metadata.philosophy_id,
        focus=week.focus.value,
        day_count=len(days),
    )

    return WeekStructure(
        structure_id=best.metadata.id,
        philosophy_id=best.metadata.philosophy_id,
        focus=week.focus,
        days=days,
        rules=best.rules,
        session_groups=best.session_groups,
        guards=best.guards,
        day_index_to_session_type=day_index_to_session_type,
    )
