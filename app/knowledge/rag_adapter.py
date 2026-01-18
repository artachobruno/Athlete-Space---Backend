"""RAG adapter for explanation-only knowledge queries.

This adapter wraps the existing RAG system for Phase 6:
- Uses existing RAG retrieval (no reimplementation)
- Returns normalized KnowledgeSnippet format
- NO influence on decisions, confidence, or execution
"""

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from app.knowledge.contracts import KnowledgeSnippet
from app.knowledge.normalizer import normalize_rag_result
from app.rag.retrieve.retriever import RagRetriever
from app.rag.types import Domain

if TYPE_CHECKING:
    from app.rag.types import RagChunk


# Default artifacts directory path (can be overridden)
_DEFAULT_ARTIFACTS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "rag_artifacts"

# Singleton retriever instance
_RETRIEVER: RagRetriever | None = None


def _get_retriever() -> RagRetriever | None:
    """Get or initialize RAG retriever from artifacts.

    Returns:
        RagRetriever instance or None if artifacts not found
    """
    global _RETRIEVER

    if _RETRIEVER is not None:
        return _RETRIEVER

    try:
        artifacts_dir = _DEFAULT_ARTIFACTS_DIR

        if not artifacts_dir.exists():
            logger.debug(
                "RAG artifacts not found, knowledge queries disabled",
                artifacts_dir=str(artifacts_dir),
            )
            return None

        _RETRIEVER = RagRetriever.from_artifacts(artifacts_dir)
        logger.info("[Phase 6 Knowledge] Loaded RAG retriever for explanations")

    except Exception:
        logger.exception("Failed to initialize RAG retriever, knowledge queries disabled")
        return None

    return _RETRIEVER


def query_existing_rag(topic: str, k: int = 3) -> list[KnowledgeSnippet]:
    """Query existing RAG corpus for explanation-only knowledge.

    This adapter uses the existing RAG retrieval system but normalizes
    output to KnowledgeSnippet format for safe inclusion in explanations.

    Args:
        topic: Topic to query (e.g., "fatigue", "tapering", "progression")
        k: Number of snippets to return (default: 3)

    Returns:
        List of normalized KnowledgeSnippet dictionaries (may be empty)

    Design:
        - Uses existing RAG retrieval (wraps, doesn't rebuild)
        - Normalizes output to safe format
        - Never modifies state
        - Never computes decisions
        - Returns empty list on failure (never raises)
    """
    retriever = _get_retriever()

    if retriever is None:
        logger.debug("RAG retriever not available, returning empty knowledge snippets")
        return []

    try:
        # Use broad defaults for explanation queries (not decision-making)
        # This allows retrieval for educational purposes without strict filtering
        domain: Domain = "training_philosophy"  # Default to philosophy for explanations
        race_type = "5k"  # Default race type (most common)
        athlete_tags: list[str] = []  # No athlete-specific filtering for explanations

        # Retrieve chunks using existing RAG system
        chunks = retriever.retrieve_chunks(
            query=topic,
            domain=domain,
            race_type=race_type,
            athlete_tags=athlete_tags,
            k=k,
        )

        # Normalize to KnowledgeSnippet format
        snippets: list[KnowledgeSnippet] = []
        for chunk in chunks:
            normalized = normalize_rag_result(chunk)
            if normalized:
                snippets.append(normalized)

        logger.debug(
            f"[Phase 6 Knowledge] Retrieved {len(snippets)} knowledge snippets for topic: {topic}"
        )

    except Exception:
        # Never raise - return empty list on any error
        logger.exception(f"RAG query failed for topic '{topic}', returning empty list")
        snippets = []

    return snippets
