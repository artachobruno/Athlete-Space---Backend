"""Context management for Coach Orchestrator Agent.

Handles loading and saving conversation history for the pydantic_ai agent.
"""

from datetime import datetime, timezone

from loguru import logger

from app.state.db import get_session
from app.state.models import CoachMessage


def load_context(athlete_id: int, limit: int = 20) -> list[dict[str, str]]:
    """Load conversation history for an athlete.

    Args:
        athlete_id: Strava athlete ID
        limit: Maximum number of messages to retrieve (default: 20)

    Returns:
        List of messages with 'role' and 'content' keys, formatted for pydantic_ai
    """
    with get_session() as db:
        messages = (
            db.query(CoachMessage).filter(CoachMessage.athlete_id == athlete_id).order_by(CoachMessage.timestamp.desc()).limit(limit).all()
        )
        # Reverse to get chronological order (oldest first)
        history = [{"role": msg.role, "content": msg.content} for msg in reversed(messages)]
        logger.info(
            "Loaded conversation history",
            athlete_id=athlete_id,
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
    with get_session() as db:
        # Save user message
        user_msg = CoachMessage(
            athlete_id=athlete_id,
            role="user",
            content=user_message,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(user_msg)

        # Save assistant message
        assistant_msg = CoachMessage(
            athlete_id=athlete_id,
            role="assistant",
            content=assistant_message,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(assistant_msg)

        db.commit()
        logger.info(
            "Saved conversation context",
            athlete_id=athlete_id,
            model_name=model_name,
            user_message_length=len(user_message),
            assistant_message_length=len(assistant_message),
        )
