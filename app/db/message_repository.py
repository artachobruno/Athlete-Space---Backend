"""Message persistence repository for long-term storage (B29).

This module provides async/background persistence of canonical Message objects
to Postgres. Persistence never blocks requests - failures are logged but never raise.

Postgres is NEVER used for prompts - Redis remains the only short-term working memory.
"""

from datetime import datetime, timezone
from uuid import UUID

from loguru import logger

from app.core.message import Message
from app.db.models import Conversation, ConversationMessage
from app.db.session import SessionLocal


def _normalize_conversation_id(conversation_id: str) -> str:
    """Normalize conversation_id for database storage.

    Strips the 'c_' prefix if present, leaving only the UUID portion.
    The database stores conversation_id as UUID type, not as a prefixed string.

    Args:
        conversation_id: Conversation ID in format c_<UUID> or <UUID>

    Returns:
        UUID string without prefix (e.g., "2423eccd-17be-406b-b48e-0d71399a762a")

    Raises:
        ValueError: If the ID (after stripping prefix) is not a valid UUID
    """
    raw_id = conversation_id

    # Strip 'c_' prefix if present
    if isinstance(raw_id, str) and raw_id.startswith("c_"):
        raw_id = raw_id[2:]

    # Validate it's a valid UUID
    try:
        UUID(raw_id)
    except ValueError as e:
        raise ValueError(
            f"Invalid conversation_id format: {conversation_id}. "
            f"Expected format: c_<UUID> or <UUID>. Error: {e}"
        ) from e

    return raw_id


def _ensure_conversation(session, conversation_id: str, user_id: str) -> None:
    """Ensure a conversation exists in the database.

    This is a get-or-create operation that ensures the conversation
    row exists before inserting messages. This is required to satisfy
    the foreign key constraint on conversation_messages.

    Args:
        session: SQLAlchemy session
        conversation_id: Normalized conversation ID (UUID string without 'c_' prefix)
        user_id: User ID who owns the conversation

    Returns:
        None (creates conversation if it doesn't exist)
    """
    # Check if conversation exists
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        # Create the conversation
        conversation = Conversation(
            id=conversation_id,
            user_id=user_id,
            status="active",
        )
        session.add(conversation)
        session.flush()  # Ensures FK is visible for subsequent inserts


def persist_message(message: Message) -> None:
    """Persist a canonical Message to Postgres asynchronously.

    This function is designed to be called from background tasks.
    It never blocks requests and never raises exceptions upstream.

    Args:
        message: Canonical Message object with all fields populated

    Returns:
        None (always succeeds from caller's perspective)

    Raises:
        Never raises - all errors are logged and swallowed
    """
    try:
        # Parse ISO-8601 timestamp string to datetime
        # Message.ts is ISO-8601 string (e.g., "2024-01-01T12:00:00+00:00" or "2024-01-01T12:00:00Z")
        ts_str = message.ts.replace("Z", "+00:00")
        try:
            ts_datetime = datetime.fromisoformat(ts_str)
            # Ensure timezone-aware (default to UTC if naive)
            if ts_datetime.tzinfo is None:
                ts_datetime = ts_datetime.replace(tzinfo=timezone.utc)
        except ValueError as e:
            logger.warning(
                "Failed to parse message timestamp, using current time",
                conversation_id=message.conversation_id,
                user_id=message.user_id,
                ts=message.ts,
                error=str(e),
            )
            ts_datetime = datetime.now(timezone.utc)

        # Create database session for this write
        # Using SessionLocal directly since we're in a background task
        session = SessionLocal()  # pyright: ignore[reportGeneralTypeIssues]
        try:
            # Normalize conversation_id: strip 'c_' prefix for database storage
            # Database stores conversation_id as UUID, not as prefixed string
            normalized_conversation_id = _normalize_conversation_id(message.conversation_id)

            # Ensure conversation exists before inserting message (FK constraint)
            _ensure_conversation(session, normalized_conversation_id, message.user_id)

            # Create ConversationMessage record
            db_message = ConversationMessage(
                conversation_id=normalized_conversation_id,
                user_id=message.user_id,
                role=message.role,
                content=message.content,
                tokens=message.tokens,
                ts=ts_datetime,
                message_metadata=message.metadata,
                # created_at is set by default in model
            )

            session.add(db_message)
            session.commit()

            logger.debug(
                "Message persisted",
                event="message_persisted",
                conversation_id=message.conversation_id,
                user_id=message.user_id,
                role=message.role,
                tokens=message.tokens,
            )
        except Exception:
            session.rollback()
            raise  # Re-raise to outer catch
        finally:
            session.close()

    except Exception:
        # Never rethrow - log and continue
        logger.exception(
            "Failed to persist message",
            conversation_id=message.conversation_id,
            user_id=message.user_id,
            role=message.role,
            tokens=message.tokens,
        )
