"""Safety collector (read-only).

Tracks safety metrics from decision data and load flags.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.db.models import DailyDecision
from app.db.session import get_session
from app.internal.ai_ops.types import SafetyStats


def collect_safety() -> SafetyStats:
    """Collect safety metrics from decisions.

    Returns:
        SafetyStats with load risk percentage, recovery alignment, summary
    """
    try:
        with get_session() as db:
            # Get recent decisions (last 7 days)
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            decisions = db.execute(
                select(DailyDecision).where(
                    DailyDecision.created_at >= cutoff,
                    DailyDecision.is_active,
                )
            ).scalars().all()

            if not decisions:
                return SafetyStats(
                    load_risk_pct=0.0,
                    recovery_aligned_pct=0.0,
                    summary="No recent decisions",
                )

            load_risk_count = 0
            recovery_aligned_count = 0
            total_with_risk = 0
            total_with_recovery = 0

            for decision in decisions:
                decision_data = decision.decision_data

                # Check for load risk indicators
                risk_level = decision_data.get("risk_level")
                if risk_level:
                    total_with_risk += 1
                    if risk_level in {"medium", "high"}:
                        load_risk_count += 1

                # Check for recovery alignment
                # This would typically come from recovery_state or similar fields
                # For now, we'll check if there's a recovery-related flag
                if "recovery" in str(decision_data).lower():
                    total_with_recovery += 1
                    # Assume aligned if recovery is mentioned (simplified)
                    recovery_aligned_count += 1

            load_risk_pct = (load_risk_count / total_with_risk * 100.0) if total_with_risk > 0 else 0.0
            recovery_aligned_pct = (
                (recovery_aligned_count / total_with_recovery * 100.0) if total_with_recovery > 0 else 100.0
            )

            # Generate summary
            summary_parts: list[str] = []
            if load_risk_pct > 20.0:
                summary_parts.append(f"High load risk: {load_risk_pct:.1f}%")
            else:
                summary_parts.append(f"Load risk: {load_risk_pct:.1f}%")

            if recovery_aligned_pct < 80.0:
                summary_parts.append(f"Recovery alignment: {recovery_aligned_pct:.1f}%")
            else:
                summary_parts.append(f"Recovery aligned: {recovery_aligned_pct:.1f}%")

            summary = "; ".join(summary_parts) if summary_parts else "No safety data"

            return SafetyStats(
                load_risk_pct=load_risk_pct,
                recovery_aligned_pct=recovery_aligned_pct,
                summary=summary,
            )

    except Exception as e:
        logger.warning(f"Failed to collect safety: {e}")
        return SafetyStats(
            load_risk_pct=0.0,
            recovery_aligned_pct=100.0,
            summary="Error collecting safety data",
        )
