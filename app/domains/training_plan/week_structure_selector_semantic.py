"""Semantic week structure selection with pure embedding pipeline.

This module implements semantic retrieval for week structure selection:
1. Load ALL structures (no filtering)
2. Embed query text
3. Score ALL candidates using cosine similarity
4. Pick best match

No filters. No thresholds. No fallbacks. Pure embedding selection.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.domains.training_plan.enums import DayType
from app.domains.training_plan.errors import InvalidSkeletonError
from app.domains.training_plan.models import DaySkeleton, MacroWeek, PlanRuntimeContext, WeekStructure
from app.domains.training_plan.observability import log_event
from app.domains.training_plan.query_builder import build_week_structure_query_text
from app.domains.training_plan.selectors.embedding_selector import select_best_by_embedding
from app.domains.training_plan.week_structure import (
    DAY_NAME_TO_INDEX,
    SESSION_TYPE_TO_DAY_TYPE,
    load_all_structures,
)
from app.domains.training_plan.week_structure_embedding import build_week_structure_canonical_text
from app.embeddings.embedding_service import get_embedding_service
from app.embeddings.vector_store import EmbeddedItem, VectorStore
from app.planning.structure.types import StructureSpec

# Cache directory
CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "embeddings"
WEEK_STRUCTURES_CACHE = CACHE_DIR / "week_structures.json"


@dataclass
class EmbeddedStructureSpec:
    """Wrapper for StructureSpec with embedding.

    Attributes:
        spec: Structure specification
        embedding: Embedding vector for the structure
    """

    spec: StructureSpec
    embedding: list[float]

    @property
    def id(self) -> str:
        """Get structure ID for logging."""
        return self.spec.metadata.id


def _load_week_structure_vector_store() -> VectorStore:
    """Load week structure vector store from cache.

    Returns:
        VectorStore with all week structure embeddings

    Raises:
        RuntimeError: If cache not found (run precompute_embeddings first)
    """
    if not WEEK_STRUCTURES_CACHE.exists():
        raise RuntimeError(
            f"Week structure embeddings cache not found: {WEEK_STRUCTURES_CACHE}\n"
            "Run: python scripts/precompute_embeddings.py --week-structures"
        )

    with Path(WEEK_STRUCTURES_CACHE).open("r", encoding="utf-8") as f:
        cache_data = json.load(f)

    items = [
        EmbeddedItem(
            id=item_data["id"],
            embedding=item_data["embedding"],
            metadata=item_data["metadata"],
        )
        for item_data in cache_data
    ]

    return VectorStore(items)


def _load_all_structures_with_embeddings() -> list[EmbeddedStructureSpec]:
    """Load all structures with their embeddings.

    Computes embeddings on-the-fly for structures missing from cache.

    Returns:
        List of EmbeddedStructureSpec objects

    Raises:
        RuntimeError: If cache not found or structures can't be loaded
    """
    # Load all structures from files
    all_structures = load_all_structures()

    # Load vector store with embeddings (may be empty if cache doesn't exist)
    try:
        vector_store = _load_week_structure_vector_store()
    except RuntimeError:
        # Cache doesn't exist - we'll compute all embeddings on-the-fly
        logger.warning(
            "Week structure embeddings cache not found. Computing embeddings on-the-fly. "
            "Run: python scripts/precompute_embeddings.py --week-structures to precompute."
        )
        vector_store = VectorStore([])

    # Get embedding service for on-the-fly computation
    embedding_service = get_embedding_service()

    # Match structures with embeddings, computing missing ones on-the-fly
    embedded_structures: list[EmbeddedStructureSpec] = []
    structures_to_compute: list[StructureSpec] = []

    for spec in all_structures:
        embedded_item = vector_store.get_item(spec.metadata.id)
        if embedded_item:
            embedded_structures.append(
                EmbeddedStructureSpec(spec=spec, embedding=embedded_item.embedding)
            )
        else:
            # Embedding missing - will compute on-the-fly
            structures_to_compute.append(spec)

    # Compute missing embeddings on-the-fly
    if structures_to_compute:
        logger.info(
            f"Computing {len(structures_to_compute)} missing embeddings on-the-fly",
            missing_ids=[s.metadata.id for s in structures_to_compute],
        )
        canonical_texts = [build_week_structure_canonical_text(spec) for spec in structures_to_compute]
        embeddings = embedding_service.embed_batch(canonical_texts)

        for spec, embedding in zip(structures_to_compute, embeddings, strict=True):
            embedded_structures.append(
                EmbeddedStructureSpec(spec=spec, embedding=embedding)
            )
            logger.debug(f"Computed embedding for structure {spec.metadata.id}")

    return embedded_structures


def _map_session_type_to_day_type(session_type: str) -> DayType:
    """Map RAG session type to DayType enum.

    Args:
        session_type: Session type from RAG

    Returns:
        Corresponding DayType enum value

    Raises:
        InvalidSkeletonError: If session type is not recognized
    """
    day_type = SESSION_TYPE_TO_DAY_TYPE.get(session_type.lower())
    if day_type is None:
        raise InvalidSkeletonError(f"Unknown session type: {session_type}")
    return day_type


def load_week_structure_semantic(
    ctx: PlanRuntimeContext,
    week: MacroWeek,
    _athlete_state: AthleteState,
    days_to_race: int,
) -> WeekStructure:
    """Load week structure using pure embedding selection.

    Pipeline:
    1. Load ALL structures (no filtering)
    2. Embed query text
    3. Score ALL candidates using cosine similarity
    4. Pick best match

    Args:
        ctx: Runtime context with plan and selected philosophy
        week: Macro week with focus
        _athlete_state: Athlete state (for validation, not used in filtering)
        days_to_race: Days until race

    Returns:
        WeekStructure with days, rules, session_groups, guards

    Raises:
        RuntimeError: If structures or embeddings can't be loaded
    """
    logger.debug(
        "Loading week structure (semantic - pure embedding)",
        philosophy_id=ctx.philosophy.philosophy_id if ctx.philosophy else None,
        focus=week.focus.value,
        race=ctx.plan.race_distance.value if ctx.plan.race_distance else None,
        days_to_race=days_to_race,
    )

    # Load ALL structures with embeddings (no filtering)
    all_embedded_structures = _load_all_structures_with_embeddings()

    if not all_embedded_structures:
        raise RuntimeError("No structures found with embeddings. Run precompute_embeddings.py first.")

    logger.info(
        "Loaded all structures for embedding selection",
        total_count=len(all_embedded_structures),
    )

    # Build query
    query_text = build_week_structure_query_text(
        ctx=ctx,
        days_to_race=days_to_race,
        current_phase=week.focus.value,
    )

    # Embed query
    embedding_service = get_embedding_service()
    embed_fn = embedding_service.embed_text

    log_event(
        "week_structure_embedding_query",
        query_text=query_text,
        query_length=len(query_text),
        candidates_count=len(all_embedded_structures),
        days_to_race=days_to_race,
    )

    # Select best using pure embedding similarity
    best_embedded = select_best_by_embedding(
        items=all_embedded_structures,
        query_text=query_text,
        embed_fn=embed_fn,
        vector_attr="embedding",
    )

    best_spec = best_embedded.spec

    log_event(
        "week_structure_final_selection",
        structure_id=best_spec.metadata.id,
        phase=best_spec.metadata.phase,
        philosophy_id=best_spec.metadata.philosophy_id,
        method="pure_embedding",
    )

    logger.info(
        "Selected week structure (pure embedding)",
        structure_id=best_spec.metadata.id,
        phase=best_spec.metadata.phase,
        philosophy_id=best_spec.metadata.philosophy_id,
    )

    return _build_week_structure_from_spec(best_spec, week)


def _build_week_structure_from_spec(spec: StructureSpec, week: MacroWeek) -> WeekStructure:
    """Build WeekStructure from StructureSpec.

    Args:
        spec: Structure specification
        week: Macro week with focus

    Returns:
        WeekStructure instance
    """
    # Convert week_pattern to DaySkeleton list
    days: list[DaySkeleton] = []
    day_index_to_session_type: dict[int, str] = {}

    for day_name, session_type in spec.week_pattern.items():
        day_index = DAY_NAME_TO_INDEX.get(day_name.lower())
        if day_index is None:
            raise InvalidSkeletonError(f"Unknown day name: {day_name}")

        day_type = _map_session_type_to_day_type(session_type)
        days.append(DaySkeleton(day_index=day_index, day_type=day_type))
        day_index_to_session_type[day_index] = session_type

    # Sort days by day_index
    days.sort(key=lambda d: d.day_index)

    return WeekStructure(
        structure_id=spec.metadata.id,
        philosophy_id=spec.metadata.philosophy_id,
        focus=week.focus,
        days=days,
        rules=spec.rules,
        session_groups=spec.session_groups,
        guards=spec.guards,
        day_index_to_session_type=day_index_to_session_type,
    )
