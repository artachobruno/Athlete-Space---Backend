"""Conversation progress service for stateful slot extraction.

Manages conversation progress state to enable:
- Cumulative slot accumulation across turns
- Awaited slot tracking for follow-up questions
- Context-aware slot resolution
"""

from datetime import date, datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.conversation_ownership import get_conversation_owner
from app.db.models import Conversation, ConversationProgress
from app.db.session import get_session


def serialize_slots_for_storage(slots: dict[str, Any]) -> dict[str, Any]:
    """Serialize slots for JSON storage (convert date objects to ISO strings).

    Args:
        slots: Slots dictionary that may contain date objects

    Returns:
        Slots dictionary with date objects converted to ISO strings
    """
    serialized: dict[str, Any] = {}
    for key, value in slots.items():
        if isinstance(value, date):
            # Convert date to ISO string for JSON storage
            serialized[key] = value.isoformat()
        elif value is None:
            # Keep None as None
            serialized[key] = None
        else:
            # Keep other types as-is (str, int, float, bool)
            serialized[key] = value
    return serialized


def deserialize_slots_from_storage(slots: dict[str, Any]) -> dict[str, date | str | int | float | bool | None]:
    """Deserialize slots from JSON storage (convert ISO date strings back to date objects).

    Args:
        slots: Slots dictionary from database (may contain ISO date strings)

    Returns:
        Slots dictionary with ISO date strings converted to date objects
    """
    deserialized: dict[str, date | str | int | float | bool | None] = {}
    for key, value in slots.items():
        if isinstance(value, str):
            # Try to parse as ISO date string
            try:
                # Try date.fromisoformat first (handles YYYY-MM-DD format)
                parsed_date = date.fromisoformat(value)
                deserialized[key] = parsed_date
            except (ValueError, AttributeError):
                try:
                    # Fallback to datetime.fromisoformat (handles YYYY-MM-DDTHH:MM:SS format)
                    parsed_date = datetime.fromisoformat(value).date()
                    deserialized[key] = parsed_date
                except (ValueError, AttributeError):
                    # Not a date string, keep as string
                    deserialized[key] = value
        elif value is None:
            deserialized[key] = None
        else:
            # Keep other types as-is (int, float, bool)
            deserialized[key] = value
    return deserialized


def get_conversation_progress(conversation_id: str) -> ConversationProgress | None:
    """Get conversation progress for a conversation.

    Args:
        conversation_id: Conversation ID (format: c_<UUID> or <UUID>)

    Returns:
        ConversationProgress or None if not found

    Note:
        The returned object is detached from the session. Access all attributes
        immediately or copy the data you need before the session closes.
        Slots are automatically deserialized (ISO date strings -> date objects).
    """
    # Convert c_<UUID> format to UUID for database query if needed
    # Database may store as UUID type, so strip the 'c_' prefix
    db_conversation_id = conversation_id
    if conversation_id.startswith("c_"):
        db_conversation_id = conversation_id[2:]  # Strip 'c_' prefix

    with get_session() as db:
        result = db.execute(select(ConversationProgress).where(ConversationProgress.conversation_id == db_conversation_id)).first()
        if result:
            progress = result[0]
            # Detach the object from the session by accessing all attributes
            # This ensures they're loaded before the session closes
            _ = progress.conversation_id
            _ = progress.intent
            _ = progress.slots
            _ = progress.awaiting_slots
            _ = progress.updated_at

            # Deserialize slots (convert ISO date strings back to date objects)
            if progress.slots:
                progress.slots = deserialize_slots_from_storage(progress.slots)

            # Expunge to detach from session
            db.expunge(progress)
            return progress
        return None


def clear_conversation_progress(conversation_id: str) -> None:
    """Clear/reset conversation progress slots.

    Used when starting a new planning intent to prevent old slots from
    poisoning new conversations.

    Args:
        conversation_id: Conversation ID to clear
    """
    db_conversation_id = conversation_id
    if conversation_id.startswith("c_"):
        db_conversation_id = conversation_id[2:]

    with get_session() as db:
        progress = db.get(ConversationProgress, db_conversation_id)
        if progress:
            progress.slots = {}
            progress.awaiting_slots = []
            progress.intent = None
            progress.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "Cleared conversation progress",
                conversation_id=conversation_id,
            )


def create_or_update_progress(
    conversation_id: str,
    intent: str | None = None,
    slots: dict[str, Any] | None = None,
    awaiting_slots: list[str] | None = None,
    user_id: str | None = None,
    clear_on_intent_change: bool = True,
) -> ConversationProgress:
    """Create or update conversation progress.

    Args:
        conversation_id: Conversation ID (format: c_<UUID> or <UUID>)
        intent: Intent name (e.g., "race_plan")
        slots: Slot values dictionary (may contain date objects)
        awaiting_slots: List of slot names we're waiting for
        user_id: Optional user ID (will be looked up if not provided)
        clear_on_intent_change: If True, clear old slots when intent changes (default: True)

    Returns:
        Updated ConversationProgress (slots are deserialized back to date objects)

    Note:
        Slots are automatically serialized (date objects -> ISO strings) before storage,
        and deserialized (ISO strings -> date objects) when returned.
        B41: Slot state is locked when awaiting_slots is empty (slots are complete).
        Locked slot state cannot be modified.

        IMPORTANT: Slots are automatically cleared when intent changes to prevent old
        conversation slots from poisoning new conversations. This ensures that starting
        a new planning conversation (e.g., "I want to train for a marathon" after
        previously planning a 5K) doesn't carry over old race_date, race_distance, etc.
    """
    # Convert c_<UUID> format to UUID for database query if needed
    # Database stores conversation_id as UUID type, so strip the 'c_' prefix
    db_conversation_id = conversation_id
    if conversation_id.startswith("c_"):
        db_conversation_id = conversation_id[2:]  # Strip 'c_' prefix

    with get_session() as db:
        # Ensure the Conversation exists before creating ConversationProgress
        # This is required to satisfy the foreign key constraint
        conversation = db.get(Conversation, db_conversation_id)
        if conversation is None:
            # Get user_id - use provided value, or look it up from ConversationOwnership
            resolved_user_id = user_id
            if resolved_user_id is None:
                resolved_user_id = get_conversation_owner(conversation_id)
            if resolved_user_id is None:
                # If still None, we can't create the conversation
                # This should not happen in production - ownership should exist
                raise ValueError(
                    f"Cannot create Conversation for {conversation_id}: user_id is required. "
                    "Either provide user_id parameter or ensure ConversationOwnership exists."
                )

            # Create the conversation
            conversation = Conversation(
                id=db_conversation_id,
                user_id=resolved_user_id,
                status="active",
            )
            db.add(conversation)
            db.flush()  # Ensure FK is visible for subsequent inserts
            logger.debug(
                "Created conversation for progress",
                conversation_id=conversation_id,
                user_id=resolved_user_id,
            )

        # Query within this session to avoid detached instance issues
        result = db.execute(select(ConversationProgress).where(ConversationProgress.conversation_id == db_conversation_id)).first()
        progress = result[0] if result else None
        now = datetime.now(timezone.utc)

        # Clear slots if intent changed (prevent old slots from poisoning new conversations)
        if clear_on_intent_change and progress and intent and progress.intent and progress.intent != intent:
            logger.info(
                "Intent changed - clearing old slots to prevent contamination",
                conversation_id=conversation_id,
                old_intent=progress.intent,
                new_intent=intent,
            )
            progress.slots = {}
            progress.awaiting_slots = []
            progress.intent = intent

        # B41: Check if slot state is locked (awaiting_slots is empty = slots complete)
        if progress and len(progress.awaiting_slots) == 0:
            # Slot state is locked - prevent mutation
            logger.warning(
                "Attempted to update locked slot state",
                conversation_id=conversation_id,
                existing_slots=progress.slots,
                attempted_slots=slots,
                attempted_awaiting_slots=awaiting_slots,
            )
            # Return existing progress without modification
            # Deserialize slots before returning
            if progress.slots:
                progress.slots = deserialize_slots_from_storage(progress.slots)
            db.expunge(progress)
            return progress

        # Serialize slots for JSON storage (convert date objects to ISO strings)
        serialized_slots: dict[str, Any] = {}
        if slots is not None:
            serialized_slots = serialize_slots_for_storage(slots)

        if progress is None:
            # Create new progress
            progress = ConversationProgress(
                conversation_id=db_conversation_id,
                intent=intent,
                slots=serialized_slots,
                awaiting_slots=awaiting_slots if awaiting_slots is not None else [],
                updated_at=now,
            )
            db.add(progress)
            logger.info(
                f"Created conversation progress - "
                f"slots_count={len(slots) if slots else 0}, "
                f"awaiting_count={len(awaiting_slots) if awaiting_slots else 0}",
                conversation_id=conversation_id,
                intent=intent,
                slots_raw=slots,
                slots_serialized=serialized_slots,
                awaiting_slots=awaiting_slots,
            )
        else:
            # Update existing progress
            if intent is not None:
                progress.intent = intent
            if slots is not None:
                progress.slots = serialized_slots
            if awaiting_slots is not None:
                progress.awaiting_slots = awaiting_slots
            progress.updated_at = now

            logger.debug(
                "Updated conversation progress",
                conversation_id=conversation_id,
                intent=progress.intent,
                slots_raw=slots,
                slots_serialized=serialized_slots,
                slots_before_update=progress.slots if progress else None,
                awaiting_slots=progress.awaiting_slots,
            )

        try:
            db.commit()
            # Deserialize slots before returning (convert ISO strings back to date objects)
            if progress.slots:
                progress.slots = deserialize_slots_from_storage(progress.slots)

            # Expunge to detach from session before returning
            # This allows the object to be used after the session closes
            db.expunge(progress)
        except SQLAlchemyError:
            db.rollback()
            logger.exception(
                f"Failed to save conversation progress (conversation_id={conversation_id})"
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
            except SQLAlchemyError:
                db.rollback()
                logger.exception(
                    f"Failed to clear conversation progress (conversation_id={conversation_id})"
                )
                raise
