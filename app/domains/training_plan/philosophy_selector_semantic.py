"""Semantic philosophy selection with pure embedding pipeline.

This module implements semantic retrieval for philosophy selection:
1. Load ALL philosophies (no filtering)
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
from app.domains.training_plan.enums import RaceDistance
from app.domains.training_plan.errors import PlannerError
from app.domains.training_plan.models import PhilosophySelection, PlanContext
from app.domains.training_plan.observability import log_event
from app.domains.training_plan.philosophy_loader import PhilosophyDoc, load_philosophies
from app.domains.training_plan.query_builder import build_philosophy_query_text
from app.domains.training_plan.selectors.embedding_selector import select_best_by_embedding
from app.embeddings.embedding_service import get_embedding_service
from app.embeddings.vector_store import EmbeddedItem, VectorStore

# Cache directory
CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "embeddings"
PHILOSOPHIES_CACHE = CACHE_DIR / "philosophies.json"

# Global cache for philosophy vector store (loaded once at startup)
_philosophy_vector_store: VectorStore | None = None
_embedded_philosophies_cache: list[EmbeddedPhilosophyDoc] | None = None


@dataclass
class EmbeddedPhilosophyDoc:
    """Wrapper for PhilosophyDoc with embedding.

    Attributes:
        philosophy: Philosophy document
        embedding: Embedding vector for the philosophy
    """

    philosophy: PhilosophyDoc
    embedding: list[float]

    @property
    def id(self) -> str:
        """Get philosophy ID for logging."""
        return self.philosophy.id


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


def _load_all_philosophies_with_embeddings() -> list[EmbeddedPhilosophyDoc]:
    """Load all philosophies with their embeddings (cached globally).

    Returns:
        List of EmbeddedPhilosophyDoc objects

    Raises:
        RuntimeError: If cache not found or philosophies can't be loaded
    """
    global _embedded_philosophies_cache

    # Return cached version if available
    if _embedded_philosophies_cache is not None:
        return _embedded_philosophies_cache

    # Load all philosophies from files
    all_philosophies = load_philosophies()

    # Load vector store with embeddings (uses global cache)
    vector_store = _get_philosophy_vector_store()

    # Match philosophies with embeddings
    embedded_philosophies: list[EmbeddedPhilosophyDoc] = []
    for philosophy in all_philosophies:
        embedded_item = vector_store.get_item(philosophy.id)
        if embedded_item:
            embedded_philosophies.append(
                EmbeddedPhilosophyDoc(philosophy=philosophy, embedding=embedded_item.embedding)
            )
        else:
            logger.warning(f"No embedding found for philosophy {philosophy.id}, skipping")

    # Cache the result
    _embedded_philosophies_cache = embedded_philosophies
    return embedded_philosophies


def _get_philosophy_vector_store() -> VectorStore:
    """Get cached philosophy vector store (loads once, then reuses).

    Returns:
        Cached VectorStore instance

    Raises:
        RuntimeError: If cache not found
    """
    global _philosophy_vector_store

    if _philosophy_vector_store is not None:
        return _philosophy_vector_store

    _philosophy_vector_store = _load_philosophy_vector_store()
    return _philosophy_vector_store


def initialize_philosophy_vector_store() -> None:
    """Initialize philosophy vector store at startup.

    This should be called once at application startup to load the vector store
    into memory and cache it globally.

    Raises:
        RuntimeError: If cache not found
    """
    logger.info("Initializing philosophy vector store cache")
    _get_philosophy_vector_store()
    # Pre-load embedded philosophies to cache them too
    _load_all_philosophies_with_embeddings()
    logger.info("Philosophy vector store initialized and cached")


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


def select_philosophy_semantic(
    ctx: PlanContext,
    athlete_state: AthleteState,
    user_preference: str | None = None,
) -> PhilosophySelection:
    """Select philosophy using pure embedding selection.

    Pipeline:
    1. Load ALL philosophies (no filtering)
    2. Embed query text
    3. Score ALL candidates using cosine similarity
    4. Pick best match

    Args:
        ctx: Plan context
        athlete_state: Athlete state
        user_preference: Optional explicit philosophy ID override

    Returns:
        PhilosophySelection

    Raises:
        PlannerError: If user preference is invalid (user override only)
        RuntimeError: If philosophies or embeddings can't be loaded
    """
    logger.info(
        "Selecting philosophy (semantic - pure embedding)",
        intent=ctx.intent.value,
        race_distance=ctx.race_distance.value if ctx.race_distance else None,
        user_preference=user_preference,
    )

    # Load all philosophies
    all_philosophies = load_philosophies()
    philosophy_dict = {p.id: p for p in all_philosophies}

    # STEP 1: Explicit user override (preserved for user preference)
    if user_preference:
        match = philosophy_dict.get(user_preference)
        if not match:
            raise PlannerError(f"Unknown philosophy '{user_preference}'")

        logger.info("Selected philosophy via user override", philosophy_id=match.id)
        return PhilosophySelection(
            philosophy_id=match.id,
            domain=match.domain,
            audience=match.audience,
        )

    # Load ALL philosophies with embeddings (no filtering, uses global cache)
    all_embedded_philosophies = _load_all_philosophies_with_embeddings()

    if not all_embedded_philosophies:
        raise RuntimeError("No philosophies found with embeddings. Run precompute_embeddings.py first.")

    logger.info(
        "Loaded all philosophies for embedding selection",
        total_count=len(all_embedded_philosophies),
    )

    # Build query
    athlete_audience = _determine_audience(athlete_state)
    race_distance_str = ctx.race_distance.value if ctx.race_distance else "all"
    domain = "ultra" if ctx.race_distance and ctx.race_distance.value == "ultra" else "running"

    query_text = build_philosophy_query_text(
        domain=domain,
        race_distance=race_distance_str,
        athlete_level=athlete_audience,
        goal=ctx.intent.value,
    )

    # Embed query
    embedding_service = get_embedding_service()
    embed_fn = embedding_service.embed_text

    log_event(
        "philosophy_embedding_query",
        query_text=query_text,
        query_length=len(query_text),
        candidates_count=len(all_embedded_philosophies),
    )

    # Select best using pure embedding similarity
    best_embedded = select_best_by_embedding(
        items=all_embedded_philosophies,
        query_text=query_text,
        embed_fn=embed_fn,
        vector_attr="embedding",
    )

    best_philosophy = best_embedded.philosophy

    log_event(
        "philosophy_final_selection",
        philosophy_id=best_philosophy.id,
        domain=best_philosophy.domain,
        audience=best_philosophy.audience,
        method="pure_embedding",
    )

    logger.info(
        "Selected philosophy (pure embedding)",
        philosophy_id=best_philosophy.id,
        domain=best_philosophy.domain,
        audience=best_philosophy.audience,
    )

    return PhilosophySelection(
        philosophy_id=best_philosophy.id,
        domain=best_philosophy.domain,
        audience=best_philosophy.audience,
    )
