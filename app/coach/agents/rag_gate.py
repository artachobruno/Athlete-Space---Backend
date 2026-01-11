"""Confidence gate for RAG usage.

This module enforces confidence gating - RAG context is only usable
if confidence is medium or high.
"""

from app.coach.rag.context import RagConfidence, RagContext


def rag_is_usable(rag_context: RagContext | None) -> bool:
    """Check if RAG context is usable (confidence-gated).

    RAG is only usable if confidence is medium or high.
    Low confidence means RAG should not influence decisions.

    Args:
        rag_context: RAG context to check (may be None)

    Returns:
        True if RAG context is usable, False otherwise
    """
    if rag_context is None:
        return False

    return rag_context.confidence in {"medium", "high"}


def get_confidence_level(rag_context: RagContext | None) -> RagConfidence | None:
    """Get confidence level from RAG context.

    Args:
        rag_context: RAG context (may be None)

    Returns:
        Confidence level or None if context is None
    """
    if rag_context is None:
        return None

    return rag_context.confidence
