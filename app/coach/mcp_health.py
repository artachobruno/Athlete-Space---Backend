"""MCP health tracking for circuit breaker pattern."""

import time
from typing import Optional

from loguru import logger

# Global state for MCP health tracking
_last_mcp_success: float | None = None
_health_check_window_seconds = 60.0


def record_mcp_success() -> None:
    """Record a successful MCP call timestamp."""
    global _last_mcp_success
    _last_mcp_success = time.time()
    logger.debug("Recorded MCP success", timestamp=_last_mcp_success)


def get_last_mcp_success() -> float | None:
    """Get the timestamp of the last successful MCP call.

    Returns:
        Unix timestamp of last success, or None if no success recorded
    """
    return _last_mcp_success


def mcp_is_healthy(now: float | None = None) -> bool:
    """Check if MCP is healthy based on recent success.

    MCP is considered healthy if there was a successful call within
    the health check window (default 60 seconds).

    Args:
        now: Current timestamp (for testing). If None, uses time.time()

    Returns:
        True if MCP is healthy, False otherwise
    """
    if now is None:
        now = time.time()

    if _last_mcp_success is None:
        # No success recorded yet - assume healthy (first call)
        return True

    time_since_success = now - _last_mcp_success
    is_healthy = time_since_success < _health_check_window_seconds

    if not is_healthy:
        logger.warning(
            "MCP health check failed",
            time_since_success=time_since_success,
            window_seconds=_health_check_window_seconds,
        )

    return is_healthy


def reset_mcp_health() -> None:
    """Reset MCP health tracking (for testing)."""
    global _last_mcp_success
    _last_mcp_success = None
