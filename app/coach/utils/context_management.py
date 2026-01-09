"""Context management for Coach Orchestrator Agent.

Handles loading and saving conversation history for the pydantic_ai agent.
"""

import threading
from datetime import datetime, timezone

from loguru import logger

from app.core.message import Message, normalize_message
from app.core.redis_conversation_store import write_message
from app.db.message_repository import persist_message
from app.db.models import CoachMessage
from app.db.session import get_session
from app.state.api_helpers import get_user_id_from_athlete_id


def load_context(athlete_id: int, limit: int = 20) -> list[dict[str, str]]:
    """Load conversation history for an athlete.

    Args:
        athlete_id: Strava athlete ID
        limit: Maximum number of messages to retrieve (default: 20)

    Returns:
        List of messages with 'role' and 'content' keys, formatted for pydantic_ai
        Note: Messages loaded from DB are already normalized (stored in canonical format)
    """
    # Convert athlete_id to user_id
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.warning("No user_id found for athlete_id, returning empty history", athlete_id=athlete_id)
        return []

    with get_session() as db:
        messages = (
            db.query(CoachMessage).filter(CoachMessage.user_id == user_id).order_by(CoachMessage.created_at.desc()).limit(limit).all()
        )
        # Reverse to get chronological order (oldest first)
        # Messages from DB are already normalized, but we validate roles
        history = []
        for msg in reversed(messages):
            # Validate role is one of the allowed values
            if msg.role not in {"user", "assistant", "system"}:
                logger.warning(
                    "Invalid role in stored message, skipping",
                    athlete_id=athlete_id,
                    user_id=user_id,
                    message_id=msg.id,
                    role=msg.role,
                )
                continue
            history.append({"role": msg.role, "content": msg.content})
        logger.info(
            "Loaded conversation history",
            athlete_id=athlete_id,
            user_id=user_id,
            message_count=len(history),
        )
        return history


def save_context(
    athlete_id: int,
    model_name: str,
    user_message: str,
    assistant_message: str,
    conversation_id: str | None = None,
) -> None:
    """Save conversation messages to database.

    Messages are normalized before storage to ensure canonical schema.

    Args:
        athlete_id: Strava athlete ID
        model_name: Name of the model used (for tracking)
        user_message: User's message (will be normalized)
        assistant_message: Assistant's response (will be normalized)
        conversation_id: Optional conversation ID for normalization
    """
    # Convert athlete_id to user_id
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.error("Cannot save context: no user_id found for athlete_id", athlete_id=athlete_id)
        return

    # Normalize messages before storage
    # If conversation_id is not provided, we still normalize but log a warning
    if conversation_id is None:
        logger.warning(
            "save_context called without conversation_id, using placeholder",
            athlete_id=athlete_id,
            user_id=user_id,
        )
        conversation_id = "c_00000000-0000-0000-0000-000000000000"  # Placeholder

    try:
        normalized_user = normalize_message(
            raw_input=user_message,
            conversation_id=conversation_id,
            user_id=user_id,
            role="user",
        )
        normalized_assistant = normalize_message(
            raw_input=assistant_message,
            conversation_id=conversation_id,
            user_id=user_id,
            role="assistant",
        )

        # Write normalized messages to Redis (B26)
        # This happens after normalization but before DB save
        # Redis failures are logged but do not block the request
        if normalized_user:
            write_message(normalized_user)

            # Persist normalized user message to Postgres (B29)
            # This happens asynchronously and never blocks the request
            threading.Thread(target=persist_message, args=(normalized_user,), daemon=True).start()
        if normalized_assistant:
            write_message(normalized_assistant)

            # Persist normalized assistant message to Postgres (B29)
            # This happens asynchronously and never blocks the request
            threading.Thread(target=persist_message, args=(normalized_assistant,), daemon=True).start()
    except ValueError as e:
        logger.error(
            "Failed to normalize messages before saving",
            athlete_id=athlete_id,
            user_id=user_id,
            error=str(e),
        )
        # Fallback: save unnormalized messages but log error
        # This should not happen in production, but we don't want to lose messages
        normalized_user = None
        normalized_assistant = None

    with get_session() as db:
        now = datetime.now(timezone.utc)

        # Save user message (use normalized content if available)
        user_content = normalized_user.content if normalized_user else user_message
        user_msg = CoachMessage(
            athlete_id=athlete_id,
            user_id=user_id,
            role="user",
            content=user_content,
            timestamp=now,
            created_at=now,
        )
        db.add(user_msg)

        # Save assistant message (use normalized content if available)
        assistant_content = normalized_assistant.content if normalized_assistant else assistant_message
        assistant_msg = CoachMessage(
            athlete_id=athlete_id,
            user_id=user_id,
            role="assistant",
            content=assistant_content,
            timestamp=now,
            created_at=now,
        )
        db.add(assistant_msg)

        db.commit()
        logger.info(
            "Saved conversation context",
            athlete_id=athlete_id,
            user_id=user_id,
            model_name=model_name,
            user_message_length=len(user_content),
            assistant_message_length=len(assistant_content),
            normalized=normalized_user is not None and normalized_assistant is not None,
        )
