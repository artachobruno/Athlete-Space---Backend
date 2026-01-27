"""System memory monitoring for observability.

This module provides system-level memory monitoring to help diagnose
OOM (Out of Memory) issues in production.
"""

import resource
import sys
from dataclasses import dataclass

from loguru import logger


@dataclass
class MemorySnapshot:
    """System memory snapshot."""

    rss_mb: float  # Resident Set Size in MB
    vms_mb: float  # Virtual Memory Size in MB
    peak_rss_mb: float  # Peak RSS since process start
    percent_used: float | None = None  # Percentage of available memory (if available)


def get_memory_snapshot() -> MemorySnapshot:
    """Get current process memory snapshot.

    Returns:
        MemorySnapshot with current memory usage
    """
    try:
        # Get process memory usage
        usage = resource.getrusage(resource.RUSAGE_SELF)
        
        # RSS (Resident Set Size) - actual physical memory used
        # getrusage returns RSS in kilobytes on Linux, bytes on macOS
        # ru_maxrss is the peak RSS since process start
        rss_raw = usage.ru_maxrss
        
        # Detect platform: macOS returns bytes, Linux returns kilobytes
        # We check if the value is suspiciously large (> 1GB in KB would be > 1M KB)
        # If > 1M, assume it's bytes, otherwise assume KB
        if rss_raw > 1_000_000:
            # Likely bytes (macOS)
            rss_mb = rss_raw / (1024.0 * 1024.0)
        else:
            # Likely kilobytes (Linux)
            rss_mb = rss_raw / 1024.0
        
        # For peak RSS, we use ru_maxrss which is the maximum RSS since process start
        peak_rss_mb = rss_mb
        
        # Virtual memory size (not available from getrusage, approximate)
        # On Linux, we could read /proc/self/status, but for cross-platform
        # we'll just use RSS as approximation
        vms_mb = rss_mb
        
        return MemorySnapshot(
            rss_mb=rss_mb,
            vms_mb=vms_mb,
            peak_rss_mb=peak_rss_mb,
            percent_used=None,
        )
    except Exception as e:
        logger.warning(f"Failed to get memory snapshot: {e}")
        return MemorySnapshot(rss_mb=0.0, vms_mb=0.0, peak_rss_mb=0.0)


def log_memory_snapshot(component: str = "system") -> None:
    """Log current memory snapshot.

    Args:
        component: Component name for logging context
    """
    snapshot = get_memory_snapshot()
    
    # Warn if memory usage is high (> 400MB for 512MB limit)
    memory_warning = ""
    if snapshot.rss_mb > 400:
        memory_warning = f" [WARNING: High memory usage - {snapshot.rss_mb:.1f}MB / 512MB limit]"
    
    logger.info(
        f"memory_snapshot{memory_warning}",
        component=component,
        rss_mb=round(snapshot.rss_mb, 2),
        vms_mb=round(snapshot.vms_mb, 2),
        peak_rss_mb=round(snapshot.peak_rss_mb, 2),
    )


def get_connection_pool_status() -> dict[str, int | str]:
    """Get database connection pool status.

    Returns:
        Dictionary with pool status metrics
    """
    try:
        from app.db.session import get_engine
        
        engine = get_engine()
        pool = engine.pool
        
        return {
            "pool_size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "invalid": pool.invalid(),
        }
    except Exception as e:
        logger.warning(f"Failed to get connection pool status: {e}")
        return {"error": str(e)}


def log_connection_pool_status() -> None:
    """Log database connection pool status."""
    status = get_connection_pool_status()
    logger.info("connection_pool_status", **status)
