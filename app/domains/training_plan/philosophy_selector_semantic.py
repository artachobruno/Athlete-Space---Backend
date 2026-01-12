"""Semantic philosophy selection with constraint → embedding → score pipeline.

This module implements semantic retrieval for philosophy selection:
1. Hard filters (domain, race distance, audience, constraints)
2. Embedding similarity search
3. Structured scoring (60% embedding, 20% phase match, 10% audience, 10% priority)
4. Deterministic selection with minimum threshold
5. Optional LLM fallback

Replaces brittle enum matching with semantic understanding.
"""

import json
from pathlib import Path

from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.domains.training_plan.enums import RaceDistance, TrainingIntent
from app.domains.training_plan.errors import PlannerError
from app.domains.training_plan.models import PhilosophySelection, PlanContext
from app.domains.training_plan.observability import log_event
from app.domains.training_plan.philosophy_loader import PhilosophyDoc, load_philosophies
from app.domains.training_plan.query_builder import build_philosophy_query_text
from app.embeddings.embedding_service import get_embedding_service
from app.embeddings.vector_store import EmbeddedItem, VectorStore

# Cache directory
CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "embeddings"
PHILOSOPHIES_CACHE = CACHE_DIR / "philosophies.json"

# Scoring weights
EMBEDDING_WEIGHT = 0.60
AUDIENCE_WEIGHT = 0.20
PRIORITY_WEIGHT = 0.20

# Minimum score threshold
MIN_SCORE_THRESHOLD = 0.72

# Ultra distances
ULTRA_DISTANCES = {"ultra"}


def _load_philosophy_vector_store() -> VectorStore:
    """Load philosophy vector store from cache.

    Returns:
        VectorStore with all philosophy embeddings

    Raises:
        RuntimeError: If cache not found (run precompute_embeddings first)
    """
    if not PHILOSOPHIES_CACHE.exists():
        raise RuntimeError(
            f"Philosophy embeddings cache not found: {PHILOSOPHIES_CACHE}\n"
            "Run: python scripts/precompute_embeddings.py --philosophies"
        )

    with Path(PHILOSOPHIES_CACHE).open("r", encoding="utf-8") as f:
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


def _determine_domain(race_distance: RaceDistance | None) -> str:
    """Determine domain from race distance.

    Args:
        race_distance: Race distance enum or None

    Returns:
        Domain string ("ultra" | "running")
    """
    if race_distance is None:
        return "running"

    if race_distance.value in ULTRA_DISTANCES:
        return "ultra"

    return "running"


def _normalize_race_type_for_matching(race_value: str) -> list[str]:
    """Normalize race distance enum value to possible RAG race type values.

    Args:
        race_value: Race distance enum value

    Returns:
        List of possible race type strings to match against
    """
    mapping: dict[str, list[str]] = {
        "half_marathon": ["half", "half_marathon"],
        "10_mile": ["10_mile", "10 mile"],
        "5k": ["5k", "5K"],
        "10k": ["10k", "10K"],
        "marathon": ["marathon"],
        "ultra": ["ultra", "50k", "50m", "100k", "100m"],
    }

    return mapping.get(race_value, [race_value])


def _passes_constraints(philosophy: PhilosophyDoc, athlete_state: AthleteState) -> bool:
    """Check if philosophy passes constraint checks.

    Args:
        philosophy: Philosophy document
        athlete_state: Athlete state

    Returns:
        True if philosophy passes all constraints, False otherwise
    """
    for req in philosophy.requires:
        if req not in athlete_state.flags:
            return False

    return all(prohibited not in athlete_state.flags for prohibited in philosophy.prohibits)


def _determine_audience(athlete_state: AthleteState) -> str:
    """Determine athlete audience from state.

    Args:
        athlete_state: Athlete state

    Returns:
        Audience string ("beginner" | "intermediate" | "advanced")
    """
    ctl = athlete_state.ctl

    if ctl < 30:
        return "beginner"
    if ctl < 50:
        return "intermediate"
    if ctl < 70:
        return "intermediate"
    return "advanced"


def _compute_structured_score(
    embedding_similarity: float,
    philosophy: PhilosophyDoc,
    athlete_audience: str,
) -> float:
    """Compute structured score combining embedding and metadata.

    Args:
        embedding_similarity: Cosine similarity from embedding (0-1)
        philosophy: Philosophy document
        athlete_audience: Athlete audience level

    Returns:
        Combined score (0-1)
    """
    # Audience match score
    if philosophy.audience in {"all", athlete_audience}:
        audience_score = 1.0
    else:
        # Soft penalty for mismatch
        audience_score = 0.5

    # Priority score (normalize to 0-1, assuming max priority is 200)
    priority_score = min(philosophy.priority / 200.0, 1.0)

    # Weighted combination
    return (
        EMBEDDING_WEIGHT * embedding_similarity
        + AUDIENCE_WEIGHT * audience_score
        + PRIORITY_WEIGHT * priority_score
    )


def select_philosophy_semantic(
    ctx: PlanContext,
    athlete_state: AthleteState,
    user_preference: str | None = None,
) -> PhilosophySelection:
    """Select philosophy using semantic retrieval with guardrails.

    Pipeline:
    1. Hard filters (domain, race distance, audience, constraints)
    2. Embedding similarity search
    3. Structured scoring
    4. Deterministic selection

    Args:
        ctx: Plan context
        athlete_state: Athlete state
        user_preference: Optional explicit philosophy ID override

    Returns:
        PhilosophySelection

    Raises:
        PlannerError: If no valid philosophy found
    """
    logger.info(
        "Selecting philosophy (semantic)",
        intent=ctx.intent.value,
        race_distance=ctx.race_distance.value if ctx.race_distance else None,
        user_preference=user_preference,
    )

    # Load all philosophies
    philosophies = load_philosophies()
    philosophy_dict = {p.id: p for p in philosophies}

    # STEP 1: Explicit user override
    if user_preference:
        match = philosophy_dict.get(user_preference)
        if not match:
            raise PlannerError(f"Unknown philosophy '{user_preference}'")

        if not _passes_constraints(match, athlete_state):
            raise PlannerError(f"User-selected philosophy '{user_preference}' fails constraints")

        logger.info("Selected philosophy via user override", philosophy_id=match.id)
        return PhilosophySelection(
            philosophy_id=match.id,
            domain=match.domain,
            audience=match.audience,
        )

    # STEP 2: Hard filters
    domain = _determine_domain(ctx.race_distance)
    athlete_audience = _determine_audience(athlete_state)

    candidates: list[PhilosophyDoc] = []
    for philosophy in philosophies:
        # Domain filter
        if philosophy.domain != domain:
            continue

        # Race distance filter
        if ctx.race_distance:
            race_value = ctx.race_distance.value
            possible_matches = _normalize_race_type_for_matching(race_value)
            if not any(match in philosophy.race_types for match in possible_matches):
                continue

        # Audience filter (soft - "all" matches any)
        if philosophy.audience not in {"all", athlete_audience}:
            continue

        # Constraint filter
        if not _passes_constraints(philosophy, athlete_state):
            continue

        candidates.append(philosophy)

    logger.debug(f"After hard filters: {len(candidates)} candidates")

    log_event(
        "philosophy_candidate_filtered",
        domain=domain,
        race_distance=ctx.race_distance.value if ctx.race_distance else "none",
        audience=athlete_audience,
        candidates_count=len(candidates),
    )

    if not candidates:
        raise PlannerError(
            f"No valid philosophy found for domain={domain}, "
            f"race={ctx.race_distance.value if ctx.race_distance else 'none'}, "
            f"audience={athlete_audience}"
        )

    # STEP 3: Embedding similarity
    try:
        vector_store = _load_philosophy_vector_store()
    except RuntimeError as e:
        logger.warning(f"Failed to load vector store, falling back to priority: {e}")
        log_event(
            "philosophy_fallback_triggered",
            reason="vector_store_unavailable",
            error=str(e),
            fallback_method="priority",
        )
        # Fallback to priority-based selection
        best = sorted(candidates, key=lambda p: p.priority, reverse=True)[0]
        return PhilosophySelection(
            philosophy_id=best.id,
            domain=best.domain,
            audience=best.audience,
        )

    # Build query
    race_distance_str = ctx.race_distance.value if ctx.race_distance else "all"
    query_text = build_philosophy_query_text(
        domain=domain,
        race_distance=race_distance_str,
        athlete_level=athlete_audience,
        goal=ctx.intent.value,
    )

    # Embed query
    embedding_service = get_embedding_service()
    query_embedding = embedding_service.embed_text(query_text)

    log_event(
        "philosophy_embedding_query",
        query_text=query_text,
        query_length=len(query_text),
        candidates_count=len(candidates),
    )

    # Search vector store
    semantic_results = vector_store.query(query_embedding, top_k=min(10, len(candidates)))

    logger.debug(
        "Semantic search results",
        query_preview=query_text[:100],
        results_count=len(semantic_results),
    )

    # STEP 4: Structured scoring
    scored_candidates: list[tuple[PhilosophyDoc, float]] = []
    for item_id, embedding_sim, _metadata in semantic_results:
        philosophy = philosophy_dict.get(item_id)
        if not philosophy or philosophy not in candidates:
            continue

        score = _compute_structured_score(embedding_sim, philosophy, athlete_audience)
        scored_candidates.append((philosophy, score))

        log_event(
            "philosophy_semantic_rank",
            philosophy_id=philosophy.id,
            embedding_similarity=round(embedding_sim, 4),
            structured_score=round(score, 4),
            audience_match=philosophy.audience in {athlete_audience, "all"},
        )

        logger.debug(
            "Scored candidate",
            philosophy_id=philosophy.id,
            embedding_sim=embedding_sim,
            score=score,
        )

    # If no semantic matches, fall back to priority
    if not scored_candidates:
        logger.warning("No semantic matches, falling back to priority")
        best = sorted(candidates, key=lambda p: p.priority, reverse=True)[0]
        return PhilosophySelection(
            philosophy_id=best.id,
            domain=best.domain,
            audience=best.audience,
        )

    # Sort by score
    scored_candidates.sort(key=lambda x: x[1], reverse=True)

    # Filter by threshold
    passing_candidates = [(p, s) for p, s in scored_candidates if s >= MIN_SCORE_THRESHOLD]

    if not passing_candidates:
        logger.warning(
            f"No candidates pass threshold {MIN_SCORE_THRESHOLD}, using best available",
            best_score=scored_candidates[0][1] if scored_candidates else 0.0,
        )
        log_event(
            "philosophy_fallback_triggered",
            reason="threshold_not_met",
            threshold=MIN_SCORE_THRESHOLD,
            best_score=scored_candidates[0][1] if scored_candidates else 0.0,
        )
        passing_candidates = [scored_candidates[0]] if scored_candidates else []

    best_philosophy, best_score = passing_candidates[0]

    log_event(
        "philosophy_final_selection",
        philosophy_id=best_philosophy.id,
        score=round(best_score, 4),
        domain=best_philosophy.domain,
        audience=best_philosophy.audience,
        method="semantic",
    )

    logger.info(
        "Selected philosophy (semantic)",
        philosophy_id=best_philosophy.id,
        score=best_score,
        domain=best_philosophy.domain,
        audience=best_philosophy.audience,
    )

    return PhilosophySelection(
        philosophy_id=best_philosophy.id,
        domain=best_philosophy.domain,
        audience=best_philosophy.audience,
    )
