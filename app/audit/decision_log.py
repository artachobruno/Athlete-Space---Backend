"""Decision audit logging.

Persist coaching decisions and their inputs for auditability.
Append-only log that preserves the reasoning trail.
"""

from datetime import datetime, timezone

from loguru import logger

from app.db.models import DecisionAudit
from app.db.session import get_session


def log_decision(
    user_id: str,
    decision_type: str,
    inputs: dict,
    output: dict,
    rationale: dict | None = None,
) -> None:
    """Persist a coaching decision and its inputs for auditability.

    This is an append-only operation. Once logged, decisions cannot be modified.

    Args:
        user_id: User ID for the decision
        decision_type: Type of decision (e.g., "no_change", "plan_revision", etc.)
        inputs: Dictionary of inputs used to make the decision
        output: Dictionary of decision output/recommendation
        rationale: Optional dictionary with explanation/rationale
    """
    logger.debug(
        f"Logging decision audit: user_id={user_id}, decision_type={decision_type}"
    )

    with get_session() as session:
        entry = DecisionAudit(
            user_id=user_id,
            timestamp=datetime.now(timezone.utc),
            decision_type=decision_type,
            inputs=inputs,
            output=output,
            rationale=rationale,
        )
        session.add(entry)
        session.commit()
