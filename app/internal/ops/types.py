"""Ops metrics data contracts (single source of truth)."""

from dataclasses import dataclass
from typing import Literal

HealthStatus = Literal["ok", "warn", "critical"]


@dataclass(frozen=True)
class ServiceHealth:
    """Health status for a service."""

    name: str
    p95_ms: int
    status: HealthStatus


@dataclass(frozen=True)
class LatencySnapshot:
    """Current latency percentiles."""

    p50: int
    p95: int
    p99: int


@dataclass(frozen=True)
class LatencyPoint:
    """Latency data point for time series."""

    time: str
    p50: int
    p95: int
    p99: int


@dataclass(frozen=True)
class TrafficSnapshot:
    """Current traffic metrics."""

    active_users_15m: int
    active_users_24h: int
    concurrent_sessions: int
    requests_per_minute: int
    executor_runs_per_minute: float
    plan_builds_per_hour: int
    tool_calls_per_minute: int


@dataclass(frozen=True)
class OpsSummary:
    """Aggregated ops metrics summary."""

    api_health: HealthStatus
    mcp_status: HealthStatus
    uptime: float
    error_rate: float
    latency: LatencySnapshot
    latency_history: list[LatencyPoint]
    sla: float
    sla_threshold: float
    services: list[ServiceHealth]
    traffic: TrafficSnapshot
