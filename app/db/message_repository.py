"""Message persistence repository for long-term storage (B29).

This module provides async/background persistence of canonical Message objects
to Postgres. Persistence never blocks requests - failures are logged but never raise.

Postgres is NEVER used for prompts - Redis remains the only short-term working memory.
"""

from datetime import datetime, timezone

from loguru import logger

from app.core.message import Message
from app.db.models import ConversationMessage
from app.db.session import SessionLocal


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
        session = SessionLocal()
        try:
            # Create ConversationMessage record
            db_message = ConversationMessage(
                conversation_id=message.conversation_id,
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
