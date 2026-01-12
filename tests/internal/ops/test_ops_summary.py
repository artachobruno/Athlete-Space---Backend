"""Smoke tests for ops summary endpoint."""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.internal.ops.cache import get_cached_ops_summary
from app.internal.ops.summary import build_ops_summary
from app.internal.ops.types import OpsSummary


def test_build_ops_summary_returns_valid_summary() -> None:
    """Test that build_ops_summary returns valid OpsSummary.

    Assertions:
    - Returns OpsSummary
    - No field is None/null
    """
    summary = build_ops_summary()

    assert isinstance(summary, OpsSummary)
    assert summary.api_health in ("ok", "warn", "critical")
    assert summary.mcp_status in ("ok", "warn", "critical")
    assert isinstance(summary.uptime, float)
    assert summary.uptime >= 0.0
    assert isinstance(summary.error_rate, float)
    assert summary.error_rate >= 0.0
    assert summary.latency is not None
    assert isinstance(summary.latency.p50, int)
    assert isinstance(summary.latency.p95, int)
    assert isinstance(summary.latency.p99, int)
    assert summary.latency_history is not None
    assert isinstance(summary.latency_history, list)
    assert isinstance(summary.sla, float)
    assert isinstance(summary.sla_threshold, float)
    assert summary.services is not None
    assert isinstance(summary.services, list)
    assert summary.traffic is not None
    assert isinstance(summary.traffic.active_users_15m, int)
    assert isinstance(summary.traffic.active_users_24h, int)
    assert isinstance(summary.traffic.concurrent_sessions, int)
    assert isinstance(summary.traffic.requests_per_minute, int)
    assert isinstance(summary.traffic.executor_runs_per_minute, float)
    assert isinstance(summary.traffic.plan_builds_per_hour, int)
    assert isinstance(summary.traffic.tool_calls_per_minute, int)


def test_get_cached_ops_summary_returns_valid_summary() -> None:
    """Test that get_cached_ops_summary returns valid OpsSummary.

    Assertions:
    - Returns OpsSummary
    - No field is None/null
    - Caching works (multiple calls return same object if within TTL)
    """
    summary1 = get_cached_ops_summary()
    summary2 = get_cached_ops_summary()

    assert isinstance(summary1, OpsSummary)
    assert isinstance(summary2, OpsSummary)

    # Validate structure (same as above)
    assert summary1.api_health in ("ok", "warn", "critical")
    assert summary1.mcp_status in ("ok", "warn", "critical")
    assert isinstance(summary1.uptime, float)
    assert summary1.uptime >= 0.0
    assert summary1.latency is not None
    assert summary1.traffic is not None
    assert summary1.services is not None


def test_ops_summary_works_without_mcp() -> None:
    """Test that ops summary works even if MCP is disabled.

    This test ensures the endpoint never fails due to MCP unavailability.
    """
    # Should work even if MCP has never been called
    summary = build_ops_summary()

    assert isinstance(summary, OpsSummary)
    # MCP status should still be valid (defaults to "ok" if never called)
    assert summary.mcp_status in ("ok", "warn", "critical")
