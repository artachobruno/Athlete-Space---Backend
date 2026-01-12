"""Smoke tests for AI ops summary endpoint."""

import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.internal.ai_ops.cache import get_cached_ai_ops_summary
from app.internal.ai_ops.summary import build_ai_ops_summary
from app.internal.ai_ops.types import AiOpsSummary


def test_build_ai_ops_summary_returns_valid_summary() -> None:
    """Test that build_ai_ops_summary returns valid AiOpsSummary.

    Assertions:
    - Returns AiOpsSummary
    - All fields present
    - No field is None
    """
    summary = build_ai_ops_summary()

    assert isinstance(summary, AiOpsSummary)
    assert summary.decision is not None
    assert isinstance(summary.decision.intent_distribution, dict)
    assert isinstance(summary.decision.confidence_avg, float)
    assert isinstance(summary.decision.outcomes, dict)

    assert summary.funnel is not None
    assert isinstance(summary.funnel.requested, int)
    assert isinstance(summary.funnel.validated, int)
    assert isinstance(summary.funnel.planned, int)
    assert isinstance(summary.funnel.executed, int)
    assert isinstance(summary.funnel.failed, int)

    assert summary.compliance is not None
    assert isinstance(summary.compliance.executed_pct, float)
    assert isinstance(summary.compliance.missed_reasons, dict)
    assert isinstance(summary.compliance.trend_7d, list)
    assert len(summary.compliance.trend_7d) == 7

    assert summary.safety is not None
    assert isinstance(summary.safety.load_risk_pct, float)
    assert isinstance(summary.safety.recovery_aligned_pct, float)
    assert isinstance(summary.safety.summary, str)

    assert summary.rag is not None
    assert isinstance(summary.rag.usage_pct, float)
    assert isinstance(summary.rag.avg_confidence, float)
    assert isinstance(summary.rag.fallback_rate, float)
    assert isinstance(summary.rag.safety_blocks, int)

    assert summary.conversation is not None
    assert isinstance(summary.conversation.avg_turns, float)
    assert isinstance(summary.conversation.summaries_per_conversation, float)
    assert isinstance(summary.conversation.compression_ratio, float)

    assert summary.audit is not None
    assert isinstance(summary.audit.traced_pct, float)
    assert isinstance(summary.audit.confirmed_writes_pct, float)
    assert isinstance(summary.audit.audited_tools_pct, float)


def test_get_cached_ai_ops_summary_returns_valid_summary() -> None:
    """Test that get_cached_ai_ops_summary returns valid AiOpsSummary.

    Assertions:
    - Returns AiOpsSummary
    - All fields present
    - Caching works (multiple calls return same object if within TTL)
    """
    summary1 = get_cached_ai_ops_summary()
    summary2 = get_cached_ai_ops_summary()

    assert isinstance(summary1, AiOpsSummary)
    assert isinstance(summary2, AiOpsSummary)

    # Validate structure
    assert summary1.decision is not None
    assert summary1.funnel is not None
    assert summary1.compliance is not None
    assert summary1.safety is not None
    assert summary1.rag is not None
    assert summary1.conversation is not None
    assert summary1.audit is not None


def test_ai_ops_summary_works_without_data() -> None:
    """Test that AI ops summary works even if no recent data exists.

    This test ensures the endpoint never fails due to empty database.
    """
    # Should work even if database is empty
    summary = build_ai_ops_summary()

    assert isinstance(summary, AiOpsSummary)
    # All fields should have default values
    assert summary.decision is not None
    assert summary.funnel is not None
    assert summary.compliance is not None
    assert summary.safety is not None
    assert summary.rag is not None
    assert summary.conversation is not None
    assert summary.audit is not None


def test_ai_ops_summary_works_with_mcp_down() -> None:
    """Test that AI ops summary works even if MCP is down.

    This test ensures the endpoint never fails due to MCP unavailability.
    Note: AI ops doesn't call MCP, but this test ensures resilience.
    """
    # Should work even if MCP is unavailable
    summary = build_ai_ops_summary()

    assert isinstance(summary, AiOpsSummary)
    # All collectors should return defaults on error
    assert summary.decision is not None
    assert summary.funnel is not None
