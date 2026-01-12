"""Audit collector (read-only).

Tracks audit and traceability metrics.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.db.models import CoachProgressEvent, DailyDecision
from app.db.session import get_session
from app.internal.ai_ops.types import AuditStats


def collect_audit_stats() -> AuditStats:
    """Collect audit and traceability metrics.

    Returns:
        AuditStats with traced percentage, confirmed writes percentage, audited tools percentage
    """
    try:
        with get_session() as db:
            # Get progress events (for traceability)
            events = db.execute(select(CoachProgressEvent)).scalars().all()

            # Get decisions (for write confirmation)
            decisions = db.execute(select(DailyDecision)).scalars().all()

            if not events and not decisions:
                return AuditStats(
                    traced_pct=0.0,
                    confirmed_writes_pct=0.0,
                    audited_tools_pct=0.0,
                )

            # Calculate traced percentage
            # A traced event has conversation_id and step_id
            traced_events = sum(1 for event in events if event.conversation_id and event.step_id)
            total_events = len(events)
            traced_pct = (traced_events / total_events * 100.0) if total_events > 0 else 0.0

            # Calculate confirmed writes percentage
            # A confirmed write is a decision that was persisted (has id and is_active)
            confirmed_writes = sum(1 for decision in decisions if decision.id and decision.is_active)
            total_writes = len(decisions)
            confirmed_writes_pct = (confirmed_writes / total_writes * 100.0) if total_writes > 0 else 0.0

            # Calculate audited tools percentage
            # An audited tool is one that has a progress event with status "completed"
            completed_events = sum(1 for event in events if event.status == "completed")
            audited_tools_pct = (completed_events / total_events * 100.0) if total_events > 0 else 0.0

            return AuditStats(
                traced_pct=traced_pct,
                confirmed_writes_pct=confirmed_writes_pct,
                audited_tools_pct=audited_tools_pct,
            )

    except Exception as e:
        logger.warning(f"Failed to collect audit stats: {e}")
        return AuditStats(
            traced_pct=0.0,
            confirmed_writes_pct=0.0,
            audited_tools_pct=0.0,
        )
