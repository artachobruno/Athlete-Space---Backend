"""Service health checker (MCP-safe).

Tracks service health based on last successful call timestamps.
Never pings MCP synchronously - uses passive monitoring only.
"""

import threading
import time

from app.internal.ops.types import HealthStatus, ServiceHealth

# Last successful MCP call timestamps (Unix timestamp)
_last_mcp_db_success: float | None = None
_last_mcp_fs_success: float | None = None
_last_mcp_db_call: float | None = None
_last_mcp_fs_call: float | None = None

MCP_OK_THRESHOLD = 2 * 60  # 2 minutes
MCP_WARN_THRESHOLD = 10 * 60  # 10 minutes

# Lock for thread safety
_services_lock = threading.Lock()


def record_mcp_db_success() -> None:
    """Record successful MCP DB call."""
    with _services_lock:
        global _last_mcp_db_success
        _last_mcp_db_success = time.time()


def record_mcp_fs_success() -> None:
    """Record successful MCP FS call."""
    with _services_lock:
        global _last_mcp_fs_success
        _last_mcp_fs_success = time.time()


def record_mcp_db_call() -> None:
    """Record MCP DB call attempt (even if failed)."""
    with _services_lock:
        global _last_mcp_db_call
        _last_mcp_db_call = time.time()


def record_mcp_fs_call() -> None:
    """Record MCP FS call attempt (even if failed)."""
    with _services_lock:
        global _last_mcp_fs_call
        _last_mcp_fs_call = time.time()


def _get_mcp_status(last_success: float | None, last_call: float | None) -> HealthStatus:
    """Get MCP status from last success timestamp.

    Args:
        last_success: Last successful call timestamp (Unix) or None
        last_call: Last call attempt timestamp (Unix) or None

    Returns:
        HealthStatus: "ok", "warn", or "critical"
    """
    now = time.time()

    # If never called, assume ok (service not used yet)
    if last_success is None and last_call is None:
        return "ok"

    # If never succeeded but has been called, check if recent
    if last_success is None:
        if last_call is None:
            return "ok"
        time_since_call = now - last_call
        if time_since_call < MCP_WARN_THRESHOLD:
            return "warn"
        return "critical"

    # Check time since last success
    time_since_success = now - last_success

    if time_since_success < MCP_OK_THRESHOLD:
        return "ok"
    if time_since_success < MCP_WARN_THRESHOLD:
        return "warn"
    return "critical"


def get_mcp_db_status() -> HealthStatus:
    """Get MCP DB server health status.

    Returns:
        HealthStatus based on last successful call
    """
    with _services_lock:
        return _get_mcp_status(_last_mcp_db_success, _last_mcp_db_call)


def get_mcp_fs_status() -> HealthStatus:
    """Get MCP FS server health status.

    Returns:
        HealthStatus based on last successful call
    """
    with _services_lock:
        return _get_mcp_status(_last_mcp_fs_success, _last_mcp_fs_call)


def get_mcp_status() -> HealthStatus:
    """Get overall MCP status (worst of DB and FS).

    Returns:
        HealthStatus: Worst status between DB and FS
    """
    db_status = get_mcp_db_status()
    fs_status = get_mcp_fs_status()

    # Return worst status
    if db_status == "critical" or fs_status == "critical":
        return "critical"
    if db_status == "warn" or fs_status == "warn":
        return "warn"
    return "ok"


def get_service_health(name: str, p95_ms: int) -> ServiceHealth:
    """Get service health from latency.

    Args:
        name: Service name
        p95_ms: P95 latency in milliseconds

    Returns:
        ServiceHealth with status derived from latency
    """
    # Simple heuristic: p95 > 1000ms = warn, > 5000ms = critical
    if p95_ms > 5000:
        status: HealthStatus = "critical"
    elif p95_ms > 1000:
        status = "warn"
    else:
        status = "ok"

    return ServiceHealth(name=name, p95_ms=p95_ms, status=status)


def get_services_health() -> list[ServiceHealth]:
    """Get health for all services.

    Returns:
        List of ServiceHealth for all tracked services
    """
    # For now, return empty list - can be extended with real service tracking
    # This is a placeholder that matches the schema
    return []
