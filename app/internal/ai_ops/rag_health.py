"""RAG health collector (read-only).

Tracks RAG usage and health metrics.
"""

from loguru import logger

from app.internal.ai_ops.types import RagStats


def collect_rag_health() -> RagStats:
    """Collect RAG health metrics.

    Returns:
        RagStats with usage percentage, confidence, fallback rate, safety blocks

    Note:
        RAG metrics are not currently tracked in the database.
        This returns placeholder values until RAG tracking is implemented.
    """
    try:
        # RAG metrics are not currently stored in the database
        # This would require:
        # - RAG adapter call counters
        # - Fallback counters
        # - Safety block counters
        # - Confidence tracking

        # For now, return defaults
        # In a real implementation, you'd query:
        # - RAG usage logs
        # - Fallback event logs
        # - Safety block logs

        return RagStats(
            usage_pct=0.0,
            avg_confidence=0.0,
            fallback_rate=0.0,
            safety_blocks=0,
        )

    except Exception as e:
        logger.warning(f"Failed to collect RAG health: {e}")
        return RagStats(
            usage_pct=0.0,
            avg_confidence=0.0,
            fallback_rate=0.0,
            safety_blocks=0,
        )
