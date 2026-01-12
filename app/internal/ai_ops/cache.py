"""AI Ops summary cache (30s TTL, in-memory only)."""

import threading
import time

from loguru import logger

from app.internal.ai_ops.summary import build_ai_ops_summary
from app.internal.ai_ops.types import AiOpsSummary

# Cache TTL (30 seconds)
CACHE_TTL_SECONDS = 30

# Cached summary and timestamp
cached_summary: AiOpsSummary | None = None
cache_timestamp: float | None = None

# Lock for thread safety
_cache_lock = threading.Lock()


def get_cached_ai_ops_summary() -> AiOpsSummary:
    """Get cached AI ops summary (refreshes if expired).

    Returns:
        AiOpsSummary (cached or fresh)
    """
    global cached_summary, cache_timestamp
    now = time.time()

    with _cache_lock:
        # Check if cache is valid
        if cached_summary is not None and cache_timestamp is not None:
            age = now - cache_timestamp
            if age < CACHE_TTL_SECONDS:
                # Cache hit
                return cached_summary

        # Cache miss or expired - rebuild
        try:
            summary = build_ai_ops_summary()
            cached_summary = summary
            cache_timestamp = now
        except Exception as e:
            logger.error(f"Failed to build AI ops summary: {e}", exc_info=True)
            # Return stale cache if available, otherwise raise
            if cached_summary is not None:
                logger.warning("Returning stale cache due to build failure")
                return cached_summary
            raise
        else:
            return summary
