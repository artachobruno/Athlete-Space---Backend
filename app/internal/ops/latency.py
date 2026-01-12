"""Latency metrics collector (pure functions).

Tracks request latencies using in-memory rolling window.
Uses approximate percentiles (no external dependencies).
"""

import threading
import time
from collections import deque

from app.internal.ops.types import LatencyPoint, LatencySnapshot

# Rolling window: keep last 1000 request latencies (last ~5-15 minutes)
LATENCY_WINDOW_SIZE = 1000

# Time buckets for history (5-minute intervals)
HISTORY_BUCKET_MINUTES = 5
HISTORY_BUCKETS = 12  # Last 60 minutes

# In-memory latency storage (milliseconds)
_latency_samples: deque[float] = deque(maxlen=LATENCY_WINDOW_SIZE)
_latency_history: deque[LatencyPoint] = deque(maxlen=HISTORY_BUCKETS)

# Lock for thread safety
_latency_lock = threading.Lock()


def record_latency_ms(latency_ms: float) -> None:
    """Record a request latency in milliseconds.

    Args:
        latency_ms: Request latency in milliseconds
    """
    with _latency_lock:
        _latency_samples.append(latency_ms)


def _calculate_percentile(sorted_data: list[float], percentile: int) -> int:
    """Calculate approximate percentile from sorted data.

    Args:
        sorted_data: Sorted list of latency values
        percentile: Percentile to calculate (0-100)

    Returns:
        Percentile value rounded to integer milliseconds
    """
    if not sorted_data:
        return 0

    if len(sorted_data) == 1:
        return int(sorted_data[0])

    index = int((percentile / 100.0) * (len(sorted_data) - 1))
    return int(sorted_data[index])


def get_latency_snapshot() -> LatencySnapshot:
    """Get current latency percentiles.

    Returns:
        LatencySnapshot with p50, p95, p99
    """
    with _latency_lock:
        samples = list(_latency_samples)

    if not samples:
        # No data yet - return zero latencies
        return LatencySnapshot(p50=0, p95=0, p99=0)

    sorted_samples = sorted(samples)
    p50 = _calculate_percentile(sorted_samples, 50)
    p95 = _calculate_percentile(sorted_samples, 95)
    p99 = _calculate_percentile(sorted_samples, 99)

    return LatencySnapshot(p50=p50, p95=p95, p99=p99)


def get_latency_history() -> list[LatencyPoint]:
    """Get latency history for charting.

    Returns:
        List of LatencyPoint (time-bucketed)
    """
    with _latency_lock:
        return list(_latency_history)
