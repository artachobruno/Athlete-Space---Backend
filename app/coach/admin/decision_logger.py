"""Decision logging for orchestration decisions.

Logs every orchestration decision with full context for debugging.
"""

from datetime import datetime, timezone

from loguru import logger
from pydantic import BaseModel

from app.coach.schemas.orchestration import OrchestrationDecision


class DecisionLogEntry(BaseModel):
    """Log entry for an orchestration decision."""

    user_id: str | None
    athlete_id: int | None
    timestamp: datetime
    input: str
    orchestrator_output: OrchestrationDecision
    tool_executed: bool
    tool_name: str | None = None
    guard_blocked: bool = False
    guard_reason: str | None = None


class DecisionLogger:
    """Logger for orchestration decisions."""

    @staticmethod
    def log(
        user_id: str | None,
        athlete_id: int | None,
        user_input: str,
        decision: OrchestrationDecision,
        tool_executed: bool,
        *,
        tool_name: str | None = None,
        guard_blocked: bool = False,
        guard_reason: str | None = None,
    ) -> None:
        """Log an orchestration decision.

        Args:
            user_id: User ID
            athlete_id: Athlete ID
            user_input: Original user message
            decision: Orchestration decision
            tool_executed: Whether a tool was actually executed
            tool_name: Name of tool executed (if any)
            guard_blocked: Whether execution guard blocked the call
            guard_reason: Reason guard blocked (if blocked)
        """
        entry = DecisionLogEntry(
            user_id=user_id,
            athlete_id=athlete_id,
            timestamp=datetime.now(timezone.utc),
            input=user_input,
            orchestrator_output=decision,
            tool_executed=tool_executed,
            tool_name=tool_name,
            guard_blocked=guard_blocked,
            guard_reason=guard_reason,
        )

        # Log to structured logger
        logger.info(
            "Orchestration decision",
            user_id=user_id,
            athlete_id=athlete_id,
            input_preview=user_input[:100],
            intent=decision.user_intent,
            horizon=decision.horizon,
            confidence=decision.confidence,
            action=decision.action,
            tool_name=decision.tool_name,
            tool_executed=tool_executed,
            guard_blocked=guard_blocked,
            guard_reason=guard_reason,
            reason=decision.reason,
        )

        # Also log full entry as JSON for debugging
        logger.debug("Full decision log entry", entry_json=entry.model_dump_json())


# Global logger instance
DECISION_LOGGER = DecisionLogger()
