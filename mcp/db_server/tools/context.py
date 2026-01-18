"""Context management tools for MCP DB server."""

import sys
import threading
from datetime import UTC, datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.core.message import Message, normalize_message
from app.core.redis_conversation_store import write_message
from app.db.message_repository import persist_message
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
        result = db.execute(
            select(StravaAccount.user_id).where(StravaAccount.athlete_id == str(athlete_id))
        ).scalar_one_or_none()
        return result


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

            # Defensive logging: capture user_id type and value before using it
            # Wrap in try-except to prevent logging errors from crashing the tool
            try:
                logger.debug(
                    "load_context_tool: Resolved user_id from athlete_id",
                    athlete_id=athlete_id,
                    user_id=user_id,
                    user_id_type=type(user_id).__name__ if user_id else None,
                    user_id_repr=repr(user_id) if user_id else None,
                )
            except Exception as log_err:
                # Log failure should not crash the tool - log to stderr as fallback
                print(f"WARNING: Logging failed in load_context_tool: {log_err}", file=sys.stderr)

            if user_id is None:
                try:
                    logger.warning(f"No user_id found for athlete_id={athlete_id}, returning empty history")
                except Exception:
                    pass  # Ignore logging errors
                return {"messages": []}

            # Validate user_id is a string (defensive check)
            if not isinstance(user_id, str):
                logger.error(
                    "load_context_tool: user_id is not a string",
                    athlete_id=athlete_id,
                    user_id=user_id,
                    user_id_type=type(user_id).__name__,
                    user_id_repr=repr(user_id),
                )
                raise MCPError("INVALID_INPUT", f"user_id must be a string, got {type(user_id).__name__}")

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
                        try:
                            logger.warning(
                                "Invalid role in stored message, skipping",
                                athlete_id=athlete_id,
                                user_id=user_id,
                                message_id=msg.id,
                                role=msg.role,
                            )
                        except Exception:
                            pass  # Ignore logging errors
                        continue
                    history.append({"role": msg.role, "content": msg.content})

                try:
                    logger.info(
                        "Loaded conversation history",
                        athlete_id=athlete_id,
                        user_id=user_id,
                        message_count=len(history),
                    )
                except Exception:
                    pass  # Ignore logging errors

                return {"messages": history}

    except SQLAlchemyError as e:
        error_msg = f"Database error loading context: {e!s}"
        try:
            logger.error(
                "load_context_tool: SQLAlchemy error",
                athlete_id=athlete_id,
                error_type=type(e).__name__,
                error_message=str(e),
                error_repr=repr(e),
                exc_info=True,
            )
        except Exception:
            pass  # Ignore logging errors to prevent cascading failures
        raise MCPError("DB_ERROR", error_msg) from e
    except KeyError as e:
        # Special handling for KeyError to capture more context
        error_msg = f"KeyError in load_context: {e!s}"
        try:
            logger.error(
                "load_context_tool: KeyError detected",
                athlete_id=athlete_id if "athlete_id" in locals() else None,
                error_type=type(e).__name__,
                error_message=str(e),
                error_repr=repr(e),
                error_args=getattr(e, "args", None),
                arguments_keys=list(arguments.keys()) if "arguments" in locals() else None,
                arguments_repr=repr(arguments) if "arguments" in locals() else None,
                exc_info=True,
            )
        except Exception:
            pass  # Ignore logging errors to prevent cascading failures
        raise MCPError("DB_ERROR", error_msg) from e
    except Exception as e:
        error_msg = f"Unexpected error loading context: {e!s}"
        try:
            logger.error(
                "load_context_tool: Unexpected error",
                athlete_id=athlete_id if "athlete_id" in locals() else None,
                error_type=type(e).__name__,
                error_message=str(e),
                error_repr=repr(e),
                error_args=getattr(e, "args", None),
                exc_info=True,
            )
        except Exception:
            pass  # Ignore logging errors to prevent cascading failures
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
    allowed_keys = {"athlete_id", "model_name", "user_message", "assistant_message", "conversation_id"}
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

    # Get conversation_id from arguments if available (optional for backward compatibility)
    conversation_id = arguments.get("conversation_id")

    try:
        # Convert athlete_id to user_id
        user_id = _get_user_id_from_athlete_id(athlete_id)
        if user_id is None:
            raise MCPError("USER_NOT_FOUND", f"No user_id found for athlete_id={athlete_id}")

        if not isinstance(user_id, str) or not user_id.strip():
            raise MCPError("INVALID_INPUT", f"Invalid user_id returned for athlete_id={athlete_id}")

        # Normalize messages before storage
        # If conversation_id is not provided, use placeholder and log warning
        if conversation_id is None:
            logger.warning(
                "save_context_tool called without conversation_id, using placeholder",
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
            write_message(normalized_user)
            write_message(normalized_assistant)

            # Persist normalized messages to Postgres (B29)
            # This happens asynchronously and never blocks the request
            # Using threading since this is not a FastAPI endpoint
            threading.Thread(target=persist_message, args=(normalized_user,), daemon=True).start()
            threading.Thread(target=persist_message, args=(normalized_assistant,), daemon=True).start()
        except ValueError as e:
            logger.error(
                "Failed to normalize messages in save_context_tool",
                athlete_id=athlete_id,
                user_id=user_id,
                error=str(e),
            )
            raise MCPError("INVALID_INPUT", f"Failed to normalize messages: {e!s}") from e

        # Truncate if needed (after normalization)
        max_content_length = 100000  # Reasonable limit for text content
        user_content = normalized_user.content
        assistant_content = normalized_assistant.content

        if len(user_content) > max_content_length:
            logger.warning(f"User message truncated from {len(user_content)} to {max_content_length} characters")
            user_content = user_content[:max_content_length]
        if len(assistant_content) > max_content_length:
            logger.warning(f"Assistant message truncated from {len(assistant_content)} to {max_content_length} characters")
            assistant_content = assistant_content[:max_content_length]

        with get_session() as db:
            # Save user message (use normalized content)
            now = datetime.now(UTC)
            try:
                user_msg = CoachMessage(
                    athlete_id=athlete_id,
                    user_id=user_id,
                    role="user",
                    content=user_content,
                    created_at=now,
                )
                db.add(user_msg)
            except Exception as e:
                logger.error(f"Failed to create user message object: {e}", exc_info=True)
                raise MCPError("DB_ERROR", f"Failed to create user message: {e!s}") from e

            # Save assistant message (use normalized content)
            try:
                assistant_msg = CoachMessage(
                    athlete_id=athlete_id,
                    user_id=user_id,
                    role="assistant",
                    content=assistant_content,
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
                user_message_length=len(user_content),
                assistant_message_length=len(assistant_content),
                conversation_id=conversation_id,
                normalized=True,
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
