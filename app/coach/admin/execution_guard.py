"""Execution guard for tool safety.

Enforces guardrails before tool execution:
- Tool enabled check
- Read/write permission check
- Max calls check
- Horizon allowed check
"""

from typing import Literal

from loguru import logger

from app.coach.admin.tool_registry import TOOL_REGISTRY
from app.coach.schemas.orchestration import OrchestrationDecision


class ExecutionGuardError(Exception):
    """Error raised when execution guard blocks a tool call."""

    pass


class ExecutionGuard:
    """Guard that enforces safety rules before tool execution."""

    def __init__(self, tool_registry=None):
        """Initialize execution guard.

        Args:
            tool_registry: Tool registry to use (defaults to global TOOL_REGISTRY)
        """
        self.registry = tool_registry or TOOL_REGISTRY
        self._call_counts: dict[str, int] = {}  # Track calls per session

    def reset_session(self) -> None:
        """Reset call counts for a new session."""
        self._call_counts.clear()

    def check(
        self,
        decision: OrchestrationDecision,
    ) -> tuple[bool, str | None]:
        """Check if a tool call is allowed.

        Args:
            decision: Orchestration decision to check

        Returns:
            Tuple of (allowed, reason_if_blocked)
        """
        # If NO_TOOL, always allow (no tool to check)
        if decision.action == "NO_TOOL":
            return True, None

        tool_name = decision.tool_name

        # Check tool enabled
        if not self.registry.is_enabled(tool_name):
            reason = f"Tool '{tool_name}' is disabled"
            logger.warning(f"Execution guard blocked: {reason}")
            return False, reason

        # Check read/write permission
        if decision.read_only and not self.registry.is_read_only(tool_name):
            # This is fine - read_only flag is informational
            pass
        elif not decision.read_only and self.registry.is_read_only(tool_name):
            reason = f"Tool '{tool_name}' is read-only but decision requires write"
            logger.warning(f"Execution guard blocked: {reason}")
            return False, reason

        # Check max calls
        max_calls = self.registry.get_max_calls(tool_name)
        if max_calls is not None:
            current_count = self._call_counts.get(tool_name, 0)
            if current_count >= max_calls:
                reason = f"Tool '{tool_name}' has exceeded max calls per session ({max_calls})"
                logger.warning(f"Execution guard blocked: {reason}")
                return False, reason

        # Check horizon allowed
        if decision.horizon != "none" and not self.registry.is_horizon_allowed(tool_name, decision.horizon):
            reason = f"Tool '{tool_name}' does not allow horizon '{decision.horizon}'"
            logger.warning(f"Execution guard blocked: {reason}")
            return False, reason

        # All checks passed
        return True, None

    def record_call(self, tool_name: str) -> None:
        """Record that a tool was called.

        Args:
            tool_name: Name of the tool that was called
        """
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1

    @staticmethod
    def downgrade_to_no_tool(decision: OrchestrationDecision, reason: str) -> OrchestrationDecision:
        """Downgrade a decision to NO_TOOL.

        Args:
            decision: Original decision
            reason: Reason for downgrade

        Returns:
            New decision with action=NO_TOOL
        """
        return OrchestrationDecision(
            user_intent=decision.user_intent,
            horizon=decision.horizon,
            confidence=decision.confidence,
            action="NO_TOOL",
            tool_name="none",
            read_only=True,
            reason=f"{decision.reason} (downgraded: {reason})",
        )


# Global guard instance
EXECUTION_GUARD = ExecutionGuard()
