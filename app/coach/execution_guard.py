"""Turn-scoped execution guard for preventing duplicate tool execution.

This module provides a guard mechanism that ensures tools execute at most once
per turn, even if the orchestrator → executor cycle is invoked multiple times
due to side effects (e.g., progress events, context saves).
"""

from collections import defaultdict
from threading import Lock

from loguru import logger


class TurnExecutionGuard:
    """Guard that prevents duplicate tool execution within a single turn.

    A turn is identified by conversation_id. The guard persists across
    orchestrator → executor re-entries within the same turn.

    Thread-safe for concurrent requests (different conversation_ids).
    """

    def __init__(self, conversation_id: str | None = None):
        """Initialize turn execution guard.

        Args:
            conversation_id: Conversation ID for this turn (optional, can be set later)
        """
        self.conversation_id = conversation_id
        self._executed_tools: set[str] = set()
        self._lock = Lock()

    def has_executed(self, tool_name: str) -> bool:
        """Check if a tool has already been executed in this turn.

        Args:
            tool_name: Name of the tool to check

        Returns:
            True if the tool has already been executed, False otherwise
        """
        with self._lock:
            return tool_name in self._executed_tools

    def mark_executed(self, tool_name: str) -> None:
        """Mark a tool as executed in this turn.

        Args:
            tool_name: Name of the tool that was executed

        Note:
            This should be called BEFORE the actual tool execution to prevent
            race conditions in case of re-entry.
        """
        with self._lock:
            if tool_name in self._executed_tools:
                logger.warning(
                    "Tool already marked as executed (idempotent call)",
                    tool_name=tool_name,
                    conversation_id=self.conversation_id,
                )
            else:
                self._executed_tools.add(tool_name)
                logger.debug(
                    "Marked tool as executed",
                    tool_name=tool_name,
                    conversation_id=self.conversation_id,
                )

    def reset(self) -> None:
        """Reset the guard (clear all executed tools).

        Useful for testing or if a turn needs to be restarted.
        """
        with self._lock:
            self._executed_tools.clear()
            logger.debug(
                "Turn execution guard reset",
                conversation_id=self.conversation_id,
            )
