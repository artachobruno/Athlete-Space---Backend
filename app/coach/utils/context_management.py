"""Context management for Coach Orchestrator Agent.

Handles loading and saving conversation history for the pydantic_ai agent.
"""

from datetime import datetime, timezone

from loguru import logger

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
        history = [{"role": msg.role, "content": msg.content} for msg in reversed(messages)]
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
) -> None:
    """Save conversation messages to database.

    Args:
        athlete_id: Strava athlete ID
        model_name: Name of the model used (for tracking)
        user_message: User's message
        assistant_message: Assistant's response
    """
    # Convert athlete_id to user_id
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.error("Cannot save context: no user_id found for athlete_id", athlete_id=athlete_id)
        return

    with get_session() as db:
        # Save user message
        user_msg = CoachMessage(
            athlete_id=athlete_id,
            user_id=user_id,
            role="user",
            content=user_message,
            created_at=datetime.now(timezone.utc),
        )
        db.add(user_msg)

        # Save assistant message
        assistant_msg = CoachMessage(
            athlete_id=athlete_id,
            user_id=user_id,
            role="assistant",
            content=assistant_message,
            created_at=datetime.now(timezone.utc),
        )
        db.add(assistant_msg)

        db.commit()
        logger.info(
            "Saved conversation context",
            athlete_id=athlete_id,
            user_id=user_id,
            model_name=model_name,
            user_message_length=len(user_message),
            assistant_message_length=len(assistant_message),
        )
