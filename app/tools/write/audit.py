"""Write-only access to decision audit logging.

Executor-only audit logging for coaching decisions.
"""

from loguru import logger

from app.audit.decision_log import log_decision


def record_decision_audit(
    user_id: str,
    decision_type: str,
    inputs: dict,
    output: dict,
    rationale: dict | None = None,
) -> None:
    """Record a coaching decision audit log.

    WRITE: Executor-only audit logging.
    This function persists coaching decisions and their inputs for auditability.

    Args:
        user_id: User ID for the decision
        decision_type: Type of decision (e.g., "no_change", "plan_revision", etc.)
        inputs: Dictionary of inputs used to make the decision
        output: Dictionary of decision output/recommendation
        rationale: Optional dictionary with explanation/rationale
    """
    logger.debug(
        f"Recording decision audit: user_id={user_id}, decision_type={decision_type}"
    )

    log_decision(
        user_id=user_id,
        decision_type=decision_type,
        inputs=inputs,
        output=output,
        rationale=rationale,
    )
