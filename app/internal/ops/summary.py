"""Ops summary assembler.

Aggregates metrics from latency, traffic, and services collectors.
Returns complete OpsSummary.
"""

import time

from loguru import logger

from app.internal.ops.latency import get_latency_history, get_latency_snapshot
from app.internal.ops.services import get_mcp_status, get_services_health
from app.internal.ops.traffic import get_traffic_snapshot
from app.internal.ops.types import HealthStatus, OpsSummary

# Process start time for uptime calculation (set at module init)
_process_start_time: float | None = None


def set_process_start_time(start_time: float | None = None) -> None:
    """Set process start time for uptime calculation.

    Args:
        start_time: Unix timestamp (defaults to current time)
    """
    global _process_start_time
    _process_start_time = start_time if start_time is not None else time.time()


def get_uptime() -> float:
    """Get process uptime in seconds.

    Returns:
        Uptime in seconds (0 if not initialized)
    """
    if _process_start_time is None:
        return 0.0
    return time.time() - _process_start_time


def build_ops_summary() -> OpsSummary:
    """Build complete ops summary from all collectors.

    Returns:
        OpsSummary with all metrics aggregated

    Raises:
        Exception: Only on catastrophic failures (most errors are handled gracefully)
    """
    # Initialize defaults
    api_health: HealthStatus = "ok"
    mcp_status: HealthStatus = "ok"
    latency_snapshot = get_latency_snapshot()
    latency_history: list = []
    services: list = []
    traffic_snapshot = get_traffic_snapshot()
    uptime = get_uptime()
    error_rate = 0.0
    sla = 99.9
    sla_threshold = 99.0

    # Collect latency (gracefully handle failures)
    try:
        latency_snapshot = get_latency_snapshot()
        latency_history = get_latency_history()
    except Exception as e:
        logger.warning(f"Failed to get latency metrics: {e}")
        # Use defaults (already set above)

    # Collect traffic (gracefully handle failures)
    try:
        traffic_snapshot = get_traffic_snapshot()
    except Exception as e:
        logger.warning(f"Failed to get traffic metrics: {e}")
        # Use defaults (already set above)

    # Collect service health (gracefully handle failures)
    try:
        services = get_services_health()
        mcp_status = get_mcp_status()
    except Exception as e:
        logger.warning(f"Failed to get service health: {e}")
        # Use defaults (already set above)

    # Calculate API health from latency (simple heuristic)
    # p95 > 5000ms = critical, > 2000ms = warn
    try:
        if latency_snapshot.p95 > 5000:
            api_health = "critical"
        elif latency_snapshot.p95 > 2000:
            api_health = "warn"
        else:
            api_health = "ok"
    except Exception as e:
        logger.warning(f"Failed to calculate API health: {e}")
        api_health = "ok"

    # Error rate and SLA are placeholders (would need error tracking)
    # For now, use defaults
    error_rate = 0.0
    sla = 99.9
    sla_threshold = 99.0

    return OpsSummary(
        api_health=api_health,
        mcp_status=mcp_status,
        uptime=uptime,
        error_rate=error_rate,
        latency=latency_snapshot,
        latency_history=latency_history,
        sla=sla,
        sla_threshold=sla_threshold,
        services=services,
        traffic=traffic_snapshot,
    )
