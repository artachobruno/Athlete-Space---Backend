"""Traffic metrics collector (pure functions).

Tracks request rates, active users, executor runs, plan builds using
in-memory counters and Redis.
"""

import asyncio
import inspect
import threading
import time
from collections import deque

import redis
from loguru import logger

from app.config.settings import settings
from app.internal.ops.types import TrafficSnapshot

# In-memory counters (reset on restart)
# These provide lightweight metrics without Prometheus infrastructure
TRAFFIC_COUNTERS: dict[str, int | float] = {
    "total_requests": 0,
    "executor_runs": 0,
    "plan_builds": 0,
    "tool_calls": 0,
}

# Request timestamps for RPM calculation (last 60 seconds)
_request_timestamps: deque[float] = deque(maxlen=1000)
_executor_run_timestamps: deque[float] = deque(maxlen=1000)
_plan_build_timestamps: deque[float] = deque(maxlen=100)
_tool_call_timestamps: deque[float] = deque(maxlen=1000)

# Lock for thread safety
_traffic_lock = threading.Lock()


def _get_redis_client() -> redis.Redis:
    """Get Redis client instance.

    Returns:
        Redis client with string decoding enabled
    """
    return redis.from_url(settings.redis_url, decode_responses=True)


def record_request() -> None:
    """Record a request (called from middleware)."""
    now = time.time()
    with _traffic_lock:
        TRAFFIC_COUNTERS["total_requests"] += 1
        _request_timestamps.append(now)


def record_executor_run() -> None:
    """Record an executor run."""
    now = time.time()
    with _traffic_lock:
        TRAFFIC_COUNTERS["executor_runs"] += 1
        _executor_run_timestamps.append(now)


def record_plan_build() -> None:
    """Record a plan build."""
    now = time.time()
    with _traffic_lock:
        TRAFFIC_COUNTERS["plan_builds"] += 1
        _plan_build_timestamps.append(now)


def record_tool_call() -> None:
    """Record a tool call."""
    now = time.time()
    with _traffic_lock:
        TRAFFIC_COUNTERS["tool_calls"] += 1
        _tool_call_timestamps.append(now)


def _count_recent_timestamps(timestamps: deque[float], window_seconds: int) -> int:
    """Count timestamps within recent window.

    Args:
        timestamps: Deque of timestamps
        window_seconds: Time window in seconds

    Returns:
        Count of timestamps within window
    """
    now = time.time()
    cutoff = now - window_seconds

    with _traffic_lock:
        recent = [ts for ts in timestamps if ts >= cutoff]

    return len(recent)


def _get_concurrent_sessions() -> int:
    """Get number of concurrent sessions from Redis.

    Returns:
        Number of active conversation keys in Redis
    """
    try:
        redis_client = _get_redis_client()
        # Scan for conversation:*:messages keys
        cursor = 0
        count = 0
        pattern = "conversation:*:messages"

        while True:
            cursor_result = redis_client.scan(cursor, match=pattern, count=100)
            if inspect.isawaitable(cursor_result):
                cursor_result = asyncio.get_event_loop().run_until_complete(cursor_result)

            cursor, keys = cursor_result
            count += len(keys)

            if cursor == 0:
                break
    except Exception as e:
        logger.warning(f"Failed to get concurrent sessions from Redis: {e}")
        return 0
    else:
        return count


def _get_active_users_15m() -> int:
    """Get active users in last 15 minutes (approximate from Redis keys).

    Returns:
        Approximate count of active users (based on conversation keys with recent TTL)
    """
    # Use concurrent sessions as approximation (conversation keys are active)
    # This is an approximation - real active users would require tracking user_id
    return _get_concurrent_sessions()


def _get_active_users_24h() -> int:
    """Get active users in last 24 hours (approximate).

    Returns:
        Approximate count (uses concurrent sessions as proxy)
    """
    # Approximation: assume ~2x concurrent sessions for 24h active
    # This is a placeholder - real tracking would require Redis user activity keys
    return max(_get_concurrent_sessions() * 2, _get_concurrent_sessions())


def get_traffic_snapshot() -> TrafficSnapshot:
    """Get current traffic metrics.

    Returns:
        TrafficSnapshot with all traffic metrics
    """
    # Requests per minute (last 60 seconds)
    rpm = _count_recent_timestamps(_request_timestamps, 60)

    # Executor runs per minute (last 60 seconds)
    executor_rpm = _count_recent_timestamps(_executor_run_timestamps, 60)

    # Plan builds per hour (last 3600 seconds)
    plan_builds_ph = _count_recent_timestamps(_plan_build_timestamps, 3600)

    # Tool calls per minute (last 60 seconds)
    tool_calls_rpm = _count_recent_timestamps(_tool_call_timestamps, 60)

    # Active users
    active_15m = _get_active_users_15m()
    active_24h = _get_active_users_24h()
    concurrent = _get_concurrent_sessions()

    return TrafficSnapshot(
        active_users_15m=active_15m,
        active_users_24h=active_24h,
        concurrent_sessions=concurrent,
        requests_per_minute=rpm,
        executor_runs_per_minute=float(executor_rpm),
        plan_builds_per_hour=plan_builds_ph,
        tool_calls_per_minute=tool_calls_rpm,
    )
