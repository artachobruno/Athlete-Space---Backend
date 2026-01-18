"""RAG output normalizer for safe explanation inclusion.

Normalizes raw RAG output to KnowledgeSnippet format.
Ensures no action verbs, prescriptions, or thresholds are included.
"""

from typing import TYPE_CHECKING

from app.knowledge.contracts import KnowledgeSnippet

if TYPE_CHECKING:
    from app.rag.types import RagChunk


def normalize_rag_result(raw_chunk: "RagChunk") -> KnowledgeSnippet | None:
    """Normalize RAG chunk to safe KnowledgeSnippet format.

    This function ensures RAG output is safe for inclusion in explanations:
    - No action verbs
    - No prescriptions
    - No thresholds
    - Only descriptive, educational content

    Args:
        raw_chunk: Raw RagChunk from RAG retrieval

    Returns:
        Normalized KnowledgeSnippet or None if normalization fails
    """
    if not raw_chunk or not raw_chunk.text:
        return None

    # Extract metadata
    metadata = raw_chunk.metadata or {}

    # Get title from metadata, fallback to chunk ID
    title = metadata.get("section_title") or metadata.get("title") or raw_chunk.chunk_id

    # Truncate text to safe excerpt (max 500 chars)
    text = raw_chunk.text.strip()
    excerpt = text[:500] + "..." if len(text) > 500 else text

    # Determine source from doc_id or metadata
    source = metadata.get("source", "internal")
    if not source or source == "unknown":
        source = "internal"

    # Extract relevance score if available (from similarity search)
    # Note: RAG retrieval doesn't return scores directly, but we can
    # use metadata if available
    relevance = float(metadata.get("relevance", 0.0)) if metadata.get("relevance") else 0.0

    return KnowledgeSnippet(
        id=raw_chunk.chunk_id,
        title=title,
        excerpt=excerpt,
        source=source,
        relevance=relevance,
    )
