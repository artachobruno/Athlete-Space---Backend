"""Logger configuration and setup."""

from __future__ import annotations

import contextlib
import contextvars
import sys

from loguru import logger

# Context variable to store user_id for logging
user_id_context: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_id", default=None)
# Context variable to store conversation_id for logging
conversation_id_context: contextvars.ContextVar[str | None] = contextvars.ContextVar("conversation_id", default=None)


def _get_user_id() -> str:
    """Get user_id from context variable.

    Returns:
        User ID string or "system" if not set (B45)
    """
    user_id = user_id_context.get()
    return user_id if user_id else "system"


def _get_conversation_id() -> str | None:
    """Get conversation_id from context variable.

    Returns:
        Conversation ID string or None if not set
    """
    return conversation_id_context.get()


def set_user_id(user_id: str | None) -> None:
    """Set user_id in context for logging.

    Args:
        user_id: User ID to set in context, or None to clear
    """
    if user_id:
        user_id_context.set(user_id)
    else:
        # Clear context by setting to None
        with contextlib.suppress(LookupError):
            user_id_context.set(None)


def set_conversation_id(conversation_id: str | None) -> None:
    """Set conversation_id in context for logging (B46).

    Args:
        conversation_id: Conversation ID to set in context, or None to clear
    """
    if conversation_id:
        conversation_id_context.set(conversation_id)
    else:
        # Clear context by setting to None
        with contextlib.suppress(LookupError):
            conversation_id_context.set(None)


def setup_logger(
    level: str = "INFO",
    log_file: str | None = None,
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> None:
    """Configure loguru logger with specified settings.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for logging. If None, only console logging.
        rotation: Log rotation size (e.g., "10 MB")
        retention: Log retention period (e.g., "7 days")
    """
    # Remove default handler
    logger.remove()

    # B45: Configure default extra fields globally
    # This ensures user_id and conversation_id are always present, even outside request scope
    logger.configure(
        extra={
            "user_id": "system",
            "conversation_id": None,
        }
    )

    # Patch the logger to always include user_id and conversation_id from context in all records
    # This creates a patched logger that adds user_id and conversation_id to the extra dict of all log records
    # Type annotation omitted - loguru's Record type from stubs doesn't expose extra dict properly
    def patcher(record) -> None:
        """Patch function to add user_id and conversation_id to log records (B45, B46).

        Args:
            record: Loguru record object (dict-like) that will be modified in place.
                   The record supports dict-like access with record["extra"] returning a dict.
        """
        # Access extra dict defensively - ensure it exists and is a dict
        # This prevents KeyError during error logging when extra might not be initialized
        try:
            # Try to access extra - loguru Record supports dict-like access
            extra = record["extra"]
            if not isinstance(extra, dict):
                # If extra exists but isn't a dict, create a new dict
                record["extra"] = {}
                extra = record["extra"]
        except (KeyError, TypeError, AttributeError):
            # If record doesn't support dict access or extra doesn't exist, create it
            record["extra"] = {}
            extra = record["extra"]

        # B45: Default to "system" if user_id is not set in context
        extra["user_id"] = _get_user_id()
        # B46: Get conversation_id from context (defaults to None from configure)
        conversation_id = _get_conversation_id()
        if conversation_id:
            extra["conversation_id"] = conversation_id

    # Apply patcher to the global logger instance
    # Note: logger.patch() returns a bound logger, but the patch is applied to the global logger
    _ = logger.patch(patcher)

    # Add console handler with format including user_id
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<yellow>user_id={extra[user_id]}</yellow> | "
            "<cyan>{file.name}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        level=level,
        colorize=True,
        enqueue=True,
    )

    # Add file handler if specified
    if log_file:
        logger.add(
            log_file,
            rotation=rotation,
            retention=retention,
            level=level,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | user_id={extra[user_id]} | {file.name}:{line} - {message}",
            enqueue=True,
        )
