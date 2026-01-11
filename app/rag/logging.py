"""Observability logging for RAG retrieval.

This module logs all retrieval operations for debugging and auditability.
"""


from loguru import logger

from app.rag.retrieve.assembler import RagContext
from app.rag.retrieve.confidence import RagConfidence
from app.rag.types import Domain, RagChunk


def log_retrieval(
    query: str,
    domain: Domain,
    race_type: str,
    athlete_tags: list[str],
    k: int,
    *,
    chunks: list[RagChunk],
    confidence: RagConfidence | None = None,
    filters_applied: list[str] | None = None,
    fallback_used: bool = False,
) -> None:
    """Log a retrieval operation.

    Args:
        query: Query text
        domain: Domain filter
        race_type: Race type filter
        athlete_tags: Athlete tags used for filtering
        k: Requested number of chunks
        chunks: Retrieved chunks
        confidence: Optional confidence score
        filters_applied: List of filter names applied
        fallback_used: Whether fallback was used
    """
    chunk_ids = [c.chunk_id for c in chunks]
    doc_ids = sorted({c.doc_id for c in chunks})

    log_data = {
        "query": query,
        "domain": domain,
        "race_type": race_type,
        "athlete_tags": athlete_tags,
        "k_requested": k,
        "chunks_returned": len(chunks),
        "chunk_ids": chunk_ids,
        "doc_ids": doc_ids,
        "filters_applied": filters_applied or [],
        "fallback_used": fallback_used,
    }

    if confidence:
        log_data["confidence_score"] = confidence.score
        log_data["confidence_reason"] = confidence.reason

    logger.info("rag_retrieval", **log_data)


def log_context_assembly(context: RagContext) -> None:
    """Log context assembly.

    Args:
        context: Assembled context
    """
    logger.info(
        "rag_context_assembly",
        num_chunks=len(context.chunks),
        citations=context.citations,
        chunk_ids=[c.chunk_id for c in context.chunks],
    )
