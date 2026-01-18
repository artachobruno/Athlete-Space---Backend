"""Read-only knowledge query tool for Phase 6.

Educational grounding only - does not influence decisions or execution.
"""

from app.knowledge.rag_adapter import query_existing_rag


def query_coaching_knowledge(topic: str, k: int = 3) -> list[dict]:
    """Query coaching knowledge base for educational content.

    READ-ONLY
    This tool retrieves knowledge snippets from the RAG corpus for
    explanation purposes only. The output is educational and does not
    influence decisions, confidence, plans, or execution.

    Args:
        topic: Topic to query (e.g., "fatigue", "tapering", "progression")
        k: Number of snippets to return (default: 3)

    Returns:
        List of knowledge snippet dictionaries with:
        - id: Snippet identifier
        - title: Snippet title
        - excerpt: Text excerpt (max 500 chars)
        - source: Source identifier
        - relevance: Relevance score (0.0-1.0)

    Design:
        - Educational only
        - No state modification
        - No decision influence
        - Returns empty list on failure (never raises)
    """
    snippets = query_existing_rag(topic, k)

    # Convert to plain dicts for JSON serialization
    return [dict(snippet) for snippet in snippets]
