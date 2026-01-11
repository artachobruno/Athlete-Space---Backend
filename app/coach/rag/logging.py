"""Observability logging for RAG integration.

This module provides structured logging for RAG usage in the orchestrator.
Logs are emitted once per turn to satisfy B50 and B63.
"""

from loguru import logger

from app.coach.rag.context import RagContext


def log_rag_usage(
    rag_context: RagContext | None,
    intent: str,
    athlete_id: int,
) -> None:
    """Log RAG usage for observability.

    This function logs RAG usage once per turn with:
    - Confidence level
    - Chunk IDs
    - Intent

    Args:
        rag_context: RAG context (may be None)
        intent: Orchestrator intent
        athlete_id: Athlete ID for context
    """
    if rag_context is None:
        logger.debug(
            "rag_not_used",
            intent=intent,
            athlete_id=athlete_id,
            reason="no_rag_context",
        )
        return

    chunk_ids = [chunk.id for chunk in rag_context.chunks]

    logger.info(
        "rag_used",
        confidence=rag_context.confidence,
        chunk_ids=chunk_ids,
        chunk_count=len(chunk_ids),
        intent=intent,
        athlete_id=athlete_id,
        query_preview=rag_context.query[:100] if rag_context.query else "",
    )
