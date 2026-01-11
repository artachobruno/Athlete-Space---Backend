"""Safety and rule-based filters for RAG retrieval.

This module enforces safety rules and gating constraints to prevent
unsafe philosophy recommendations.
"""


from app.rag.types import RagChunk


def filter_by_athlete_tags(chunks: list[RagChunk], athlete_tags: list[str]) -> list[RagChunk]:
    """Filter chunks based on athlete tags and safety rules.

    Safety rules:
    - If 'injury_prone' in athlete_tags → exclude medium/high risk chunks
    - If 'novice' in athlete_tags → exclude advanced philosophies
    - Enforce requires/prohibits constraints

    Args:
        chunks: Chunks to filter
        athlete_tags: List of athlete tags (e.g., ['injury_prone', 'novice'])

    Returns:
        Filtered list of chunks
    """
    if not athlete_tags:
        return chunks

    athlete_tags_lower = [tag.lower() for tag in athlete_tags]
    filtered: list[RagChunk] = []

    for chunk in chunks:
        # Rule: injury_prone → exclude medium/high risk
        if "injury_prone" in athlete_tags_lower:
            risk_level = chunk.metadata.get("risk_level", "").lower()
            if risk_level in {"medium", "high"}:
                continue

        # Rule: novice → exclude advanced philosophies
        if "novice" in athlete_tags_lower:
            audience = chunk.metadata.get("audience", "").lower()
            if audience in {"advanced", "expert"}:
                continue

        # Check requires: chunk requires tags that athlete must have
        requires_str = chunk.metadata.get("requires", "")
        if requires_str:
            required_tags = [r.strip().lower() for r in requires_str.split(",") if r.strip()]
            athlete_tags_lower_set = set(athlete_tags_lower)
            if not all(req in athlete_tags_lower_set for req in required_tags):
                continue

        # Check prohibits: chunk prohibits tags that athlete must not have
        prohibits_str = chunk.metadata.get("prohibits", "")
        if prohibits_str:
            prohibited_tags = [p.strip().lower() for p in prohibits_str.split(",") if p.strip()]
            athlete_tags_lower_set = set(athlete_tags_lower)
            if any(proh in athlete_tags_lower_set for proh in prohibited_tags):
                continue

        filtered.append(chunk)

    return filtered


def is_retrieval_safe(chunks: list[RagChunk], min_chunks: int = 1) -> bool:
    """Check if retrieval result is safe to use.

    Args:
        chunks: Retrieved chunks
        min_chunks: Minimum number of chunks required for safe retrieval

    Returns:
        True if retrieval is safe, False otherwise
    """
    return len(chunks) >= min_chunks
