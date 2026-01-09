"""Conversation progress service for stateful slot extraction.

Manages conversation progress state to enable:
- Cumulative slot accumulation across turns
- Awaited slot tracking for follow-up questions
- Context-aware slot resolution
"""

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import ConversationProgress
from app.db.session import get_session


def get_conversation_progress(conversation_id: str) -> ConversationProgress | None:
    """Get conversation progress for a conversation.

    Args:
        conversation_id: Conversation ID

    Returns:
        ConversationProgress or None if not found
    """
    with get_session() as db:
        result = db.execute(select(ConversationProgress).where(ConversationProgress.conversation_id == conversation_id)).first()
        if result:
            return result[0]
        return None


def create_or_update_progress(
    conversation_id: str,
    intent: str | None = None,
    slots: dict[str, Any] | None = None,
    awaiting_slots: list[str] | None = None,
) -> ConversationProgress:
    """Create or update conversation progress.

    Args:
        conversation_id: Conversation ID
        intent: Intent name (e.g., "race_plan")
        slots: Slot values dictionary
        awaiting_slots: List of slot names we're waiting for

    Returns:
        Updated ConversationProgress
    """
    with get_session() as db:
        progress = get_conversation_progress(conversation_id)
        now = datetime.now(timezone.utc)

        if progress is None:
            # Create new progress
            progress = ConversationProgress(
                conversation_id=conversation_id,
                intent=intent,
                slots=slots if slots is not None else {},
                awaiting_slots=awaiting_slots if awaiting_slots is not None else [],
                updated_at=now,
            )
            db.add(progress)
            logger.info(
                "Created conversation progress",
                conversation_id=conversation_id,
                intent=intent,
                slots=slots,
                awaiting_slots=awaiting_slots,
            )
        else:
            # Update existing progress
            if intent is not None:
                progress.intent = intent
            if slots is not None:
                progress.slots = slots
            if awaiting_slots is not None:
                progress.awaiting_slots = awaiting_slots
            progress.updated_at = now

            logger.debug(
                "Updated conversation progress",
                conversation_id=conversation_id,
                intent=progress.intent,
                slots=progress.slots,
                awaiting_slots=progress.awaiting_slots,
            )

        try:
            db.commit()
        except SQLAlchemyError as e:
            db.rollback()
            logger.error(
                "Failed to save conversation progress",
                conversation_id=conversation_id,
                error=str(e),
                exc_info=True,
            )
            raise

        return progress


def clear_progress(conversation_id: str) -> None:
    """Clear conversation progress (e.g., when intent is completed).

    Args:
        conversation_id: Conversation ID
    """
    with get_session() as db:
        progress = get_conversation_progress(conversation_id)
        if progress:
            db.delete(progress)
            try:
                db.commit()
                logger.info("Cleared conversation progress", conversation_id=conversation_id)
            except SQLAlchemyError as e:
                db.rollback()
                logger.error(
                    "Failed to clear conversation progress",
                    conversation_id=conversation_id,
                    error=str(e),
                    exc_info=True,
                )
                raise
