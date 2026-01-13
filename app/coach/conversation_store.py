"""Conversation store abstraction for message storage.

This module provides a high-level interface for storing messages in conversations.
It wraps the underlying Redis-based storage with a clean API.
"""

from datetime import datetime, timezone
from typing import Literal

from loguru import logger

from app.core.message import Message, normalize_message
from app.core.redis_conversation_store import write_message
from app.db.message_repository import persist_message


class ConversationStore:
    """Conversation store for appending messages to conversations.

    This class provides a clean interface for storing messages with support for:
    - Progress messages (transient, can be replaced/updated)
    - Final messages (persistent, authoritative)
    - Metadata for custom message types
    """

    @staticmethod
    async def append_message(
        conversation_id: str,
        user_id: str,
        role: Literal["user", "assistant", "system"] | None,
        content: str,
        *,
        message_type: str | None = None,
        progress_stage: str | None = None,
        metadata: dict[str, str] | None = None,
        transient: bool = False,
        show_plan: bool = False,
        planned_weeks: list | None = None,
    ) -> None:
        """Append a message to a conversation.

        Args:
            conversation_id: Conversation ID
            user_id: User ID
            role: Message role ("user", "assistant", "system")
            content: Message content
            message_type: Optional message type (e.g., "progress", "final")
            progress_stage: Optional progress stage (for progress messages)
            metadata: Optional metadata dictionary
            transient: If True, message is transient and can be replaced/updated
            show_plan: If True, indicates this message should show the plan card
            planned_weeks: Optional list of planned weeks (for final messages)

        Note:
            Transient messages are stored in Redis but not persisted to database.
            Final messages are both stored in Redis and persisted to database.
        """
        # Build metadata dictionary
        message_metadata: dict[str, str] = metadata.copy() if metadata else {}
        if message_type:
            message_metadata["type"] = message_type
        if progress_stage:
            message_metadata["progress_stage"] = progress_stage
        if transient:
            message_metadata["transient"] = "true"
        if show_plan:
            message_metadata["show_plan"] = "true"
        if planned_weeks is not None:
            # Store planned_weeks count in metadata
            message_metadata["planned_weeks_count"] = str(len(planned_weeks))

        # Normalize message
        normalized_message = normalize_message(
            raw_input=content,
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
        )

        # Update metadata (normalize_message creates a new message with default metadata)
        normalized_message.metadata = message_metadata

        # Write to Redis (always)
        write_message(normalized_message)

        # Persist to database only if not transient
        if not transient:
            try:
                persist_message(normalized_message)
                logger.debug(
                    "Message persisted to database",
                    conversation_id=conversation_id,
                    role=role,
                    message_type=message_type,
                )
            except Exception as e:
                # Persistence failures are non-fatal - log and continue
                logger.warning(
                    "Failed to persist message to database (non-fatal)",
                    conversation_id=conversation_id,
                    role=role,
                    error=str(e),
                )

        logger.debug(
            "Message appended to conversation",
            conversation_id=conversation_id,
            role=role,
            message_type=message_type,
            progress_stage=progress_stage,
            transient=transient,
        )
