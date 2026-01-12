"""AI Ops summary assembler.

Aggregates metrics from all collectors.
Returns complete AiOpsSummary with partial failure handling.
"""

from loguru import logger

from app.internal.ai_ops.audit import collect_audit_stats
from app.internal.ai_ops.compliance import collect_compliance
from app.internal.ai_ops.conversation_health import collect_conversation_health
from app.internal.ai_ops.decision_analytics import collect_decision_analytics
from app.internal.ai_ops.planning_funnel import collect_planning_funnel
from app.internal.ai_ops.rag_health import collect_rag_health
from app.internal.ai_ops.safety import collect_safety
from app.internal.ai_ops.types import (
    AiOpsSummary,
    AuditStats,
    ComplianceStats,
    ConversationStats,
    DecisionAnalytics,
    PlanningFunnelStats,
    RagStats,
    SafetyStats,
)


def build_ai_ops_summary() -> AiOpsSummary:
    """Build complete AI ops summary from all collectors.

    Returns:
        AiOpsSummary with all metrics aggregated

    Note:
        Partial failures are handled gracefully - each collector returns defaults on error.
    """
    # Initialize defaults
    decision = DecisionAnalytics(intent_distribution={}, confidence_avg=0.0, outcomes={})
    funnel = PlanningFunnelStats(requested=0, validated=0, planned=0, executed=0, failed=0)
    compliance = ComplianceStats(executed_pct=0.0, missed_reasons={}, trend_7d=[0.0] * 7)
    safety = SafetyStats(load_risk_pct=0.0, recovery_aligned_pct=100.0, summary="No data")
    rag = RagStats(usage_pct=0.0, avg_confidence=0.0, fallback_rate=0.0, safety_blocks=0)
    conversation = ConversationStats(avg_turns=0.0, summaries_per_conversation=0.0, compression_ratio=0.0)
    audit = AuditStats(traced_pct=0.0, confirmed_writes_pct=0.0, audited_tools_pct=0.0)

    # Collect decision analytics (gracefully handle failures)
    try:
        decision = collect_decision_analytics()
    except Exception as e:
        logger.warning(f"Failed to get decision analytics: {e}")
        # Use defaults (already set above)

    # Collect planning funnel (gracefully handle failures)
    try:
        funnel = collect_planning_funnel()
    except Exception as e:
        logger.warning(f"Failed to get planning funnel: {e}")
        # Use defaults (already set above)

    # Collect compliance (gracefully handle failures)
    try:
        compliance = collect_compliance()
    except Exception as e:
        logger.warning(f"Failed to get compliance: {e}")
        # Use defaults (already set above)

    # Collect safety (gracefully handle failures)
    try:
        safety = collect_safety()
    except Exception as e:
        logger.warning(f"Failed to get safety: {e}")
        # Use defaults (already set above)

    # Collect RAG health (gracefully handle failures)
    try:
        rag = collect_rag_health()
    except Exception as e:
        logger.warning(f"Failed to get RAG health: {e}")
        # Use defaults (already set above)

    # Collect conversation health (gracefully handle failures)
    try:
        conversation = collect_conversation_health()
    except Exception as e:
        logger.warning(f"Failed to get conversation health: {e}")
        # Use defaults (already set above)

    # Collect audit stats (gracefully handle failures)
    try:
        audit = collect_audit_stats()
    except Exception as e:
        logger.warning(f"Failed to get audit stats: {e}")
        # Use defaults (already set above)

    return AiOpsSummary(
        decision=decision,
        funnel=funnel,
        compliance=compliance,
        safety=safety,
        rag=rag,
        conversation=conversation,
        audit=audit,
    )
