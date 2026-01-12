"""Decision analytics collector (read-only).

Aggregates decision metrics from daily_decisions table.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import func, select

from app.db.models import DailyDecision
from app.db.session import get_session
from app.internal.ai_ops.types import DecisionAnalytics


def collect_decision_analytics() -> DecisionAnalytics:
    """Collect decision analytics from last 24 hours.

    Returns:
        DecisionAnalytics with intent distribution, avg confidence, outcomes
    """
    try:
        with get_session() as db:
            # Get decisions from last 24 hours
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            decisions = db.execute(
                select(DailyDecision).where(
                    DailyDecision.created_at >= cutoff,
                    DailyDecision.is_active,
                )
            ).scalars().all()

            if not decisions:
                # Return defaults if no data
                return DecisionAnalytics(
                    intent_distribution={},
                    confidence_avg=0.0,
                    outcomes={},
                )

            # Extract metrics from decision_data JSON
            intent_counts: dict[str, int] = {}
            confidence_sum = 0.0
            confidence_count = 0
            outcome_counts: dict[str, int] = {}

            for decision in decisions:
                decision_data = decision.decision_data

                # Extract intent (from recommendation_type or decision_data)
                intent = decision.recommendation_type or decision_data.get("recommendation", "unknown")
                intent_counts[intent] = intent_counts.get(intent, 0) + 1

                # Extract confidence
                confidence_obj = decision_data.get("confidence")
                if isinstance(confidence_obj, dict):
                    confidence_value = confidence_obj.get("value", 0.0)
                    if isinstance(confidence_value, (int, float)):
                        confidence_sum += float(confidence_value)
                        confidence_count += 1
                elif isinstance(confidence_obj, (int, float)):
                    confidence_sum += float(confidence_obj)
                    confidence_count += 1

                # Extract outcome (from recommendation or decision_data)
                outcome = decision.recommendation_type or decision_data.get("recommendation", "unknown")
                outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

            # Calculate distributions (percentages)
            total_decisions = len(decisions)
            intent_distribution: dict[str, float] = {
                intent: (count / total_decisions) * 100.0
                for intent, count in intent_counts.items()
            }

            outcomes: dict[str, float] = {
                outcome: (count / total_decisions) * 100.0
                for outcome, count in outcome_counts.items()
            }

            confidence_avg = confidence_sum / confidence_count if confidence_count > 0 else 0.0

            return DecisionAnalytics(
                intent_distribution=intent_distribution,
                confidence_avg=confidence_avg,
                outcomes=outcomes,
            )

    except Exception as e:
        logger.warning(f"Failed to collect decision analytics: {e}")
        # Return defaults on error
        return DecisionAnalytics(
            intent_distribution={},
            confidence_avg=0.0,
            outcomes={},
        )
