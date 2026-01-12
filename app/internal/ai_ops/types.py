"""AI Ops Metrics data contracts (single source of truth)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionAnalytics:
    intent_distribution: dict[str, float]
    confidence_avg: float
    outcomes: dict[str, float]


@dataclass(frozen=True)
class PlanningFunnelStats:
    requested: int
    validated: int
    planned: int
    executed: int
    failed: int


@dataclass(frozen=True)
class ComplianceStats:
    executed_pct: float
    missed_reasons: dict[str, float]
    trend_7d: list[float]


@dataclass(frozen=True)
class SafetyStats:
    load_risk_pct: float
    recovery_aligned_pct: float
    summary: str


@dataclass(frozen=True)
class RagStats:
    usage_pct: float
    avg_confidence: float
    fallback_rate: float
    safety_blocks: int


@dataclass(frozen=True)
class ConversationStats:
    avg_turns: float
    summaries_per_conversation: float
    compression_ratio: float


@dataclass(frozen=True)
class AuditStats:
    traced_pct: float
    confirmed_writes_pct: float
    audited_tools_pct: float


@dataclass(frozen=True)
class AiOpsSummary:
    decision: DecisionAnalytics
    funnel: PlanningFunnelStats
    compliance: ComplianceStats
    safety: SafetyStats
    rag: RagStats
    conversation: ConversationStats
    audit: AuditStats
