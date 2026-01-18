"""Read-only access to decision audit logs.

Retrieve coaching decisions for review and accountability.
"""

from datetime import datetime

from loguru import logger
from sqlalchemy import select

from app.db.models import DecisionAudit
from app.db.session import get_session


def get_recent_decisions(
    user_id: str,
    limit: int = 20,
) -> list[dict]:
    """Retrieve recent coaching decisions for a user.

    READ-ONLY: Retrieve recent coaching decisions.
    Returns decisions ordered by timestamp (most recent first).

    Args:
        user_id: User ID
        limit: Maximum number of decisions to return (default: 20)

    Returns:
        List of decision dictionaries with:
        - id: Decision ID
        - timestamp: When the decision was made
        - decision_type: Type of decision
        - inputs: Inputs used for the decision
        - output: Decision output/recommendation
        - rationale: Optional rationale/explanation
    """
    logger.debug(f"Retrieving recent decisions: user_id={user_id}, limit={limit}")

    with get_session() as session:
        decisions = (
            session.execute(
                select(DecisionAudit)
                .where(DecisionAudit.user_id == user_id)
                .order_by(DecisionAudit.timestamp.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

        return [
            {
                "id": decision.id,
                "timestamp": decision.timestamp,
                "decision_type": decision.decision_type,
                "inputs": decision.inputs,
                "output": decision.output,
                "rationale": decision.rationale,
            }
            for decision in decisions
        ]
