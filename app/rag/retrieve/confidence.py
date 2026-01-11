"""Confidence scoring for RAG retrieval.

This module computes confidence scores based on similarity spread,
number of chunks, and domain coverage.
"""

from dataclasses import dataclass

from app.rag.types import RagChunk


@dataclass
class RagConfidence:
    """Retrieval confidence score with explanation."""

    score: float  # 0.0-1.0
    reason: str


def compute_confidence(
    chunks: list[RagChunk],
    similarity_scores: list[float] | None = None,
    min_chunks: int = 1,
    ideal_chunks: int = 5,
) -> RagConfidence:
    """Compute retrieval confidence score.

    Signals considered:
    - Number of surviving chunks
    - Similarity score spread (if available)
    - Domain coverage

    Thresholds:
    - < 0.4 → fallback
    - 0.4-0.7 → cautious
    - > 0.7 → safe

    Args:
        chunks: Retrieved chunks
        similarity_scores: Optional similarity scores for chunks
        min_chunks: Minimum chunks expected
        ideal_chunks: Ideal number of chunks

    Returns:
        RagConfidence with score and reason
    """
    num_chunks = len(chunks)

    # Base score from chunk count
    if num_chunks == 0:
        return RagConfidence(
            score=0.0,
            reason="No chunks retrieved after filtering",
        )

    if num_chunks < min_chunks:
        return RagConfidence(
            score=0.3,
            reason=f"Only {num_chunks} chunk(s) retrieved, below minimum {min_chunks}",
        )

    # Chunk count component (0.0-0.5)
    chunk_score = min(0.5, (num_chunks / ideal_chunks) * 0.5)

    # Similarity spread component (0.0-0.3)
    similarity_score = 0.0
    if similarity_scores and len(similarity_scores) > 0:
        max_sim = max(similarity_scores)
        min_sim = min(similarity_scores)
        avg_sim = sum(similarity_scores) / len(similarity_scores)

        # High average similarity is good
        similarity_score = min(0.3, avg_sim * 0.3)

        # If spread is too large, reduce score
        if max_sim - min_sim > 0.3:
            similarity_score *= 0.7

    # Domain coverage component (0.0-0.2)
    domain_coverage = 0.0
    if chunks:
        unique_domains = len({c.metadata.get("domain", "") for c in chunks})
        domain_coverage = min(0.2, (unique_domains / 2.0) * 0.2)

    total_score = chunk_score + similarity_score + domain_coverage
    total_score = min(1.0, max(0.0, total_score))

    # Generate reason
    reasons: list[str] = []
    reasons.append(f"Retrieved {num_chunks} chunk(s)")

    if similarity_scores:
        avg_sim = sum(similarity_scores) / len(similarity_scores)
        reasons.append(f"average similarity {avg_sim:.2f}")

    unique_domains = len({c.metadata.get("domain", "") for c in chunks})
    reasons.append(f"{unique_domains} domain(s)")

    reason = "; ".join(reasons)

    return RagConfidence(score=total_score, reason=reason)
