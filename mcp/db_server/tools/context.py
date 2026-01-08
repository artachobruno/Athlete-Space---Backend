"""Context management tools for MCP DB server."""

import sys
from datetime import UTC, datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.db.models import CoachMessage, StravaAccount
from app.db.session import get_session
from mcp.db_server.errors import MCPError


def _get_user_id_from_athlete_id(athlete_id: int) -> str | None:
    """Get user_id from athlete_id via StravaAccount table.

    Args:
        athlete_id: Strava athlete ID

    Returns:
        User ID (Clerk user ID) or None if not found
    """
    with get_session() as db:
        result = db.execute(select(StravaAccount.user_id).where(StravaAccount.athlete_id == str(athlete_id))).first()
        if result:
            return result[0]
        return None


def load_context_tool(arguments: dict) -> dict:
    """Load conversation history for an athlete.

    Contract: load_context.json
    """
    athlete_id = arguments.get("athlete_id")
    limit = arguments.get("limit", 20)

    # Validate inputs
    if athlete_id is None:
        raise MCPError("INVALID_INPUT", "Missing required field: athlete_id")
    if not isinstance(athlete_id, int):
        raise MCPError("INVALID_INPUT", "athlete_id must be an integer")
    if not isinstance(limit, int) or limit <= 0:
        raise MCPError("INVALID_LIMIT", "Limit must be a positive integer")

    try:
        # Convert athlete_id to user_id
        user_id = _get_user_id_from_athlete_id(athlete_id)
        if user_id is None:
            logger.warning(f"No user_id found for athlete_id={athlete_id}, returning empty history")
            return {"messages": []}

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

            return {"messages": history}

    except SQLAlchemyError as e:
        error_msg = f"Database error loading context: {e!s}"
        logger.error(error_msg, exc_info=True)
        raise MCPError("DB_ERROR", error_msg) from e
    except Exception as e:
        error_msg = f"Unexpected error loading context: {e!s}"
        logger.error(error_msg, exc_info=True)
        raise MCPError("DB_ERROR", error_msg) from e


def save_context_tool(arguments: dict) -> dict:
    """Save conversation messages to database.

    Contract: save_context.json
    """
    # Strictly validate arguments - only accept expected keys
    athlete_id = arguments.get("athlete_id")
    model_name = arguments.get("model_name")
    user_message = arguments.get("user_message")
    assistant_message = arguments.get("assistant_message")

    # Log unexpected keys for debugging
    allowed_keys = {"athlete_id", "model_name", "user_message", "assistant_message"}
    unexpected_keys = set(arguments.keys()) - allowed_keys
    if unexpected_keys:
        logger.warning(
            f"save_context received unexpected keys: {unexpected_keys}",
            arguments_keys=list(arguments.keys()),
        )

    # Validate inputs
    if athlete_id is None:
        raise MCPError("INVALID_INPUT", "Missing required field: athlete_id")
    if not isinstance(athlete_id, int):
        raise MCPError("INVALID_INPUT", "athlete_id must be an integer")
    if not model_name or not isinstance(model_name, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid model_name")
    if not user_message or not isinstance(user_message, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid user_message")
    if not assistant_message or not isinstance(assistant_message, str):
        raise MCPError("INVALID_INPUT", "Missing or invalid assistant_message")

    # Sanitize message content - ensure they're strings and not too large
    user_message = str(user_message).strip()
    assistant_message = str(assistant_message).strip()
    max_content_length = 100000  # Reasonable limit for text content

    if len(user_message) > max_content_length:
        logger.warning(f"User message truncated from {len(user_message)} to {max_content_length} characters")
        user_message = user_message[:max_content_length]
    if len(assistant_message) > max_content_length:
        logger.warning(f"Assistant message truncated from {len(assistant_message)} to {max_content_length} characters")
        assistant_message = assistant_message[:max_content_length]

    try:
        # Convert athlete_id to user_id
        user_id = _get_user_id_from_athlete_id(athlete_id)
        if user_id is None:
            raise MCPError("USER_NOT_FOUND", f"No user_id found for athlete_id={athlete_id}")

        if not isinstance(user_id, str) or not user_id.strip():
            raise MCPError("INVALID_INPUT", f"Invalid user_id returned for athlete_id={athlete_id}")

        with get_session() as db:
            # Save user message
            now = datetime.now(UTC)
            try:
                user_msg = CoachMessage(
                    athlete_id=athlete_id,
                    user_id=user_id,
                    role="user",
                    content=user_message,
                    created_at=now,
                )
                db.add(user_msg)
            except Exception as e:
                logger.error(f"Failed to create user message object: {e}", exc_info=True)
                raise MCPError("DB_ERROR", f"Failed to create user message: {e!s}") from e

            # Save assistant message
            try:
                assistant_msg = CoachMessage(
                    athlete_id=athlete_id,
                    user_id=user_id,
                    role="assistant",
                    content=assistant_message,
                    created_at=now,
                )
                db.add(assistant_msg)
            except Exception as e:
                logger.error(f"Failed to create assistant message object: {e}", exc_info=True)
                raise MCPError("DB_ERROR", f"Failed to create assistant message: {e!s}") from e

            try:
                db.commit()
            except SQLAlchemyError as e:
                db.rollback()
                logger.error(f"Failed to commit messages to database: {e}", exc_info=True)
                raise MCPError("DB_ERROR", f"Failed to save messages: {e!s}") from e

            logger.info(
                "Saved conversation context",
                athlete_id=athlete_id,
                user_id=user_id,
                model_name=model_name,
                user_message_length=len(user_message),
                assistant_message_length=len(assistant_message),
            )

            return {"success": True, "message": "Context saved successfully"}

    except SQLAlchemyError as e:
        error_msg = f"Database error saving context: {e!s}"
        logger.error(error_msg, exc_info=True)
        raise MCPError("DB_ERROR", error_msg) from e
    except MCPError:
        raise
    except Exception as e:
        error_msg = f"Unexpected error saving context: {e!s}"
        logger.error(error_msg, exc_info=True)
        raise MCPError("DB_ERROR", error_msg) from e
