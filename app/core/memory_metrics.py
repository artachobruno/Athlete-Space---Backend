"""Conversation memory observability (B37).

This module provides structured observability for conversation memory operations
without changing behavior. All metrics are logging-only (no side effects).

Core principles:
1. No side effects — logging + metrics only
2. No new dependencies required (Prometheus optional)
3. Structured logs first, metrics second
4. Conversation-scoped observability (always log conversation_id)
5. Never log raw user content
"""

from dataclasses import dataclass

from loguru import logger


@dataclass
class MemoryMetrics:
    """Memory metrics for observability.

    All fields are optional to allow partial metrics in different contexts.
    """

    conversation_id: str
    redis_message_count: int = 0
    redis_token_count: int = 0
    prompt_token_count: int | None = None
    summary_version: int | None = None
    summary_present: bool = False


def log_memory_metrics(
    *,
    event: str,
    metrics: MemoryMetrics,
    extra: dict[str, str | int | float | bool | None] | None = None,
) -> None:
    """Log memory metrics with structured format.

    This helper ensures consistent logging format across all memory operations.
    Never logs raw user content.

    Args:
        event: Event name (e.g., "memory_history_loaded", "prompt_built")
        metrics: MemoryMetrics dataclass with conversation metrics
        extra: Optional extra fields to include in log
    """
    log_data: dict[str, str | int | float | bool | None] = {
        "event": event,
        "conversation_id": metrics.conversation_id,
        "redis_message_count": metrics.redis_message_count,
        "redis_token_count": metrics.redis_token_count,
    }

    if metrics.prompt_token_count is not None:
        log_data["prompt_token_count"] = metrics.prompt_token_count

    if metrics.summary_version is not None:
        log_data["summary_version"] = metrics.summary_version

    log_data["summary_present"] = metrics.summary_present

    if extra:
        log_data.update(extra)

    logger.info(event, **log_data)


# In-process counters (reset on restart)
# These provide lightweight metrics without Prometheus infrastructure
MEMORY_COUNTERS: dict[str, int] = {
    "summaries_created": 0,
    "compactions_run": 0,
    "token_truncations": 0,
}


def increment_memory_counter(counter_name: str) -> None:
    """Increment a memory counter.

    Args:
        counter_name: Counter name (must be in MEMORY_COUNTERS)
    """
    if counter_name in MEMORY_COUNTERS:
        MEMORY_COUNTERS[counter_name] += 1
    else:
        logger.warning(
            "Attempted to increment unknown memory counter",
            counter_name=counter_name,
            available_counters=list(MEMORY_COUNTERS.keys()),
        )


def log_memory_counters_snapshot() -> None:
    """Log current memory counter values.

    Useful for periodic snapshots without Prometheus.
    """
    logger.info("memory_counters_snapshot", **MEMORY_COUNTERS)
