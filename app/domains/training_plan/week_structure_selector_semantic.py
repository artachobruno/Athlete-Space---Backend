"""Semantic week structure selection with constraint → embedding → score pipeline.

This module implements semantic retrieval for week structure selection:
1. Hard filters (philosophy namespace, race distance, audience, phase, days_to_race)
2. Embedding similarity search
3. Structured scoring (60% embedding, 20% phase match, 10% audience, 10% days_to_race)
4. Deterministic selection with minimum threshold
5. Graceful fallback to priority-based selection

Replaces brittle exact matching with semantic understanding.
"""

import json
from pathlib import Path

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.domains.training_plan.enums import DayType, WeekFocus
from app.domains.training_plan.errors import InvalidSkeletonError
from app.domains.training_plan.models import DaySkeleton, MacroWeek, PlanRuntimeContext, WeekStructure
from app.domains.training_plan.observability import log_event
from app.domains.training_plan.query_builder import build_week_structure_query_text
from app.domains.training_plan.week_structure import (
    DAY_NAME_TO_INDEX,
    SESSION_TYPE_TO_DAY_TYPE,
    load_structures_from_philosophy,
)
from app.embeddings.embedding_service import get_embedding_service
from app.embeddings.vector_store import EmbeddedItem, VectorStore
from app.planning.structure.types import StructureSpec

# Cache directory
CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "embeddings"
WEEK_STRUCTURES_CACHE = CACHE_DIR / "week_structures.json"

# Scoring weights
EMBEDDING_WEIGHT = 0.60
PHASE_WEIGHT = 0.20
AUDIENCE_WEIGHT = 0.10
DAYS_TO_RACE_WEIGHT = 0.10

# Minimum score threshold
MIN_SCORE_THRESHOLD = 0.72


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


def _compute_structured_score(
    embedding_similarity: float,
    spec: StructureSpec,
    target_phase: str,
    target_audience: str,
    days_to_race: int,
) -> float:
    """Compute structured score combining embedding and metadata.

    Args:
        embedding_similarity: Cosine similarity from embedding (0-1)
        spec: Structure specification
        target_phase: Target phase (week.focus.value)
        target_audience: Target audience
        days_to_race: Days until race

    Returns:
        Combined score (0-1)
    """
    # Phase match score
    if spec.metadata.phase == target_phase:
        phase_score = 1.0
    else:
        # Soft penalty for phase mismatch (base -> build is acceptable)
        phase_fallback: dict[str, str] = {"base": "build", "recovery": "build", "exploration": "build"}
        if target_phase in phase_fallback and spec.metadata.phase == phase_fallback[target_phase]:
            phase_score = 0.8
        else:
            phase_score = 0.3

    # Audience match score
    if spec.metadata.audience in {"all", target_audience}:
        audience_score = 1.0
    else:
        audience_score = 0.5

    # Days to race match score
    if spec.metadata.days_to_race_min <= days_to_race <= spec.metadata.days_to_race_max:
        days_score = 1.0
    else:
        # Soft penalty based on distance from range
        if days_to_race < spec.metadata.days_to_race_min:
            distance = spec.metadata.days_to_race_min - days_to_race
        else:
            distance = days_to_race - spec.metadata.days_to_race_max

        # Normalize penalty (max penalty at 30+ days away)
        days_score = max(0.0, 1.0 - (distance / 30.0))

    # Weighted combination
    return (
        EMBEDDING_WEIGHT * embedding_similarity
        + PHASE_WEIGHT * phase_score
        + AUDIENCE_WEIGHT * audience_score
        + DAYS_TO_RACE_WEIGHT * days_score
    )


def load_week_structure_semantic(
    ctx: PlanRuntimeContext,
    week: MacroWeek,
    _athlete_state: AthleteState,
    days_to_race: int,
) -> WeekStructure:
    """Load week structure using semantic retrieval with guardrails.

    Pipeline:
    1. Hard filters (philosophy namespace, race distance, audience)
    2. Embedding similarity search
    3. Structured scoring
    4. Deterministic selection

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

    logger.debug(
        "Loading week structure (semantic)",
        philosophy_id=ctx.philosophy.philosophy_id,
        focus=week.focus.value,
        race=ctx.plan.race_distance.value,
        days_to_race=days_to_race,
    )

    # STEP 1: Hard filters - load structures from philosophy namespace
    all_structures = load_structures_from_philosophy(
        domain=ctx.philosophy.domain,
        philosophy_id=ctx.philosophy.philosophy_id,
    )

    # Filter by hard constraints
    candidates: list[StructureSpec] = []
    for spec in all_structures:
        # Philosophy namespace (already guaranteed by _load_structures_from_philosophy)
        if spec.metadata.philosophy_id != ctx.philosophy.philosophy_id:
            continue

        # Race distance
        if not spec.metadata.race_types or ctx.plan.race_distance.value not in spec.metadata.race_types:
            continue

        # Audience (soft filter - "all" matches any)
        if spec.metadata.audience not in {"all", ctx.philosophy.audience}:
            continue

        candidates.append(spec)

    logger.debug(f"After hard filters: {len(candidates)} candidates")

    log_event(
        "week_structure_candidate_filtered",
        philosophy_id=ctx.philosophy.philosophy_id,
        race_distance=ctx.plan.race_distance.value,
        audience=ctx.philosophy.audience,
        focus=week.focus.value,
        candidates_count=len(candidates),
    )

    if not candidates:
        raise InvalidSkeletonError(
            f"No plan_structure found for philosophy={ctx.philosophy.philosophy_id}, "
            f"race={ctx.plan.race_distance.value}, audience={ctx.philosophy.audience}"
        )

    # STEP 2: Embedding similarity
    try:
        vector_store = _load_week_structure_vector_store()
    except RuntimeError as e:
        logger.warning(f"Failed to load vector store, falling back to priority: {e}")
        log_event(
            "week_structure_fallback_triggered",
            reason="vector_store_unavailable",
            error=str(e),
            fallback_method="priority",
        )
        # Fallback to priority-based selection with phase matching
        phase_matches = [s for s in candidates if s.metadata.phase == week.focus.value]
        if phase_matches:
            candidates = phase_matches

        # Filter by days_to_race
        days_matches = [
            s
            for s in candidates
            if s.metadata.days_to_race_min <= days_to_race <= s.metadata.days_to_race_max
        ]
        if days_matches:
            candidates = days_matches

        if not candidates:
            raise InvalidSkeletonError(
                f"No plan_structure found for philosophy={ctx.philosophy.philosophy_id}, "
                f"focus={week.focus.value}, race={ctx.plan.race_distance.value}, "
                f"audience={ctx.philosophy.audience}, days_to_race={days_to_race}"
            ) from None

        best = sorted(candidates, key=lambda s: s.metadata.priority, reverse=True)[0]
        return _build_week_structure_from_spec(best, week)

    # Build query
    query_text = build_week_structure_query_text(
        ctx=ctx,
        days_to_race=days_to_race,
        current_phase=week.focus.value,
    )

    # Embed query
    embedding_service = get_embedding_service()
    query_embedding = embedding_service.embed_text(query_text)

    log_event(
        "week_structure_embedding_query",
        query_text=query_text,
        query_length=len(query_text),
        candidates_count=len(candidates),
        days_to_race=days_to_race,
    )

    # Search vector store
    semantic_results = vector_store.query(query_embedding, top_k=min(10, len(candidates)))

    logger.debug(
        "Semantic search results",
        query_preview=query_text[:100],
        results_count=len(semantic_results),
    )

    # STEP 3: Structured scoring
    structure_dict = {s.metadata.id: s for s in candidates}
    scored_candidates: list[tuple[StructureSpec, float]] = []

    for item_id, embedding_sim, _metadata in semantic_results:
        spec = structure_dict.get(item_id)
        if not spec:
            continue

        score = _compute_structured_score(
            embedding_sim,
            spec,
            target_phase=week.focus.value,
            target_audience=ctx.philosophy.audience,
            days_to_race=days_to_race,
        )
        scored_candidates.append((spec, score))

        log_event(
            "week_structure_semantic_rank",
            structure_id=spec.metadata.id,
            phase=spec.metadata.phase,
            embedding_similarity=round(embedding_sim, 4),
            structured_score=round(score, 4),
            phase_match=spec.metadata.phase == week.focus.value,
            days_to_race_in_range=(
                spec.metadata.days_to_race_min <= days_to_race <= spec.metadata.days_to_race_max
            ),
        )

        logger.debug(
            "Scored candidate",
            structure_id=spec.metadata.id,
            phase=spec.metadata.phase,
            embedding_sim=embedding_sim,
            score=score,
        )

    # If no semantic matches, fall back to priority
    if not scored_candidates:
        logger.warning("No semantic matches, falling back to priority")
        log_event(
            "week_structure_fallback_triggered",
            reason="no_semantic_matches",
            fallback_method="priority",
        )
        # Try phase match first
        phase_matches = [s for s in candidates if s.metadata.phase == week.focus.value]
        if phase_matches:
            candidates = phase_matches

        best = sorted(candidates, key=lambda s: s.metadata.priority, reverse=True)[0]
        return _build_week_structure_from_spec(best, week)

    # Sort by score
    scored_candidates.sort(key=lambda x: x[1], reverse=True)

    # Filter by threshold
    passing_candidates = [(s, sc) for s, sc in scored_candidates if sc >= MIN_SCORE_THRESHOLD]

    if not passing_candidates:
        logger.warning(
            f"No candidates pass threshold {MIN_SCORE_THRESHOLD}, using best available",
            best_score=scored_candidates[0][1] if scored_candidates else 0.0,
        )
        log_event(
            "week_structure_fallback_triggered",
            reason="threshold_not_met",
            threshold=MIN_SCORE_THRESHOLD,
            best_score=scored_candidates[0][1] if scored_candidates else 0.0,
        )
        passing_candidates = [scored_candidates[0]] if scored_candidates else []

    best_spec, best_score = passing_candidates[0]

    log_event(
        "week_structure_final_selection",
        structure_id=best_spec.metadata.id,
        phase=best_spec.metadata.phase,
        score=round(best_score, 4),
        method="semantic",
    )

    logger.info(
        "Selected week structure (semantic)",
        structure_id=best_spec.metadata.id,
        phase=best_spec.metadata.phase,
        score=best_score,
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
