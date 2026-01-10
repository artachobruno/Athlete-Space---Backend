"""Admin endpoint for conversation memory observability (B37).

This module provides an admin-only endpoint for debugging conversation memory state.
This endpoint returns memory metrics without exposing user content.
"""

from datetime import datetime, timezone

import redis
from fastapi import APIRouter, HTTPException
from loguru import logger

from app.config.settings import settings
from app.core.conversation_summary import get_latest_conversation_summary
from app.core.redis_conversation_store import get_recent_messages

router = APIRouter(prefix="/admin/conversations", tags=["admin"])


def _get_redis_client() -> redis.Redis:
    """Get Redis client instance.

    Returns:
        Redis client with string decoding enabled
    """
    return redis.from_url(settings.redis_url, decode_responses=True)


def _get_redis_key(conversation_id: str) -> str:
    """Construct Redis key for conversation messages.

    Args:
        conversation_id: Conversation ID

    Returns:
        Redis key string
    """
    return f"conversation:{conversation_id}:messages"


def _get_ttl_seconds(conversation_id: str) -> int | None:
    """Get TTL for conversation key in Redis.

    Args:
        conversation_id: Conversation ID

    Returns:
        TTL in seconds if key exists, None otherwise
    """
    try:
        redis_client = _get_redis_client()
        key = _get_redis_key(conversation_id)
        ttl_result = redis_client.ttl(key)

        # Handle type - ttl() returns int, but type checker sees ResponseT | None
        if not isinstance(ttl_result, int):
            return None
        ttl = ttl_result
    except Exception as e:
        logger.debug(
            "Failed to get TTL from Redis",
            conversation_id=conversation_id,
            error=str(e),
        )
        return None
    else:
        return ttl if ttl >= 0 else None


@router.get("/{conversation_id}/memory")
def get_conversation_memory(conversation_id: str) -> dict[str, str | int | None]:
    """Get conversation memory snapshot for debugging.

    This endpoint returns memory metrics without exposing user content.
    Useful for debugging "forgotten context" issues.

    Args:
        conversation_id: Conversation ID in format c_<UUID>

    Returns:
        Dictionary with memory metrics:
        - redis_message_count: Number of messages in Redis
        - redis_token_count: Total tokens in Redis messages
        - summary_version: Latest summary version (if exists)
        - last_summary_at: ISO timestamp of latest summary (if exists)
        - ttl_seconds: Redis TTL in seconds (if key exists)

    Raises:
        HTTPException: If conversation_id format is invalid
    """
    # Validate conversation_id format
    if not conversation_id or not conversation_id.startswith("c_"):
        raise HTTPException(status_code=400, detail="Invalid conversation_id format. Must start with 'c_'")

    try:
        # Get Redis messages
        messages = get_recent_messages(conversation_id, limit=100)
        redis_message_count = len(messages)
        redis_token_count = sum(msg.tokens or 0 for msg in messages)

        # Get latest summary
        latest_summary = get_latest_conversation_summary(conversation_id)
        summary_version: int | None = None
        last_summary_at: str | None = None

        if latest_summary:
            summary_version = latest_summary.get("version")
            created_at = latest_summary.get("created_at")
            if created_at:
                if isinstance(created_at, datetime):
                    last_summary_at = created_at.isoformat()
                elif isinstance(created_at, str):
                    last_summary_at = created_at
                else:
                    last_summary_at = str(created_at)

        # Get TTL
        ttl_seconds = _get_ttl_seconds(conversation_id)

        result: dict[str, str | int | None] = {
            "redis_message_count": redis_message_count,
            "redis_token_count": redis_token_count,
            "summary_version": summary_version,
            "last_summary_at": last_summary_at,
            "ttl_seconds": ttl_seconds,
        }

        logger.info(
            "memory_state_snapshot",
            conversation_id=conversation_id,
            redis_message_count=redis_message_count,
            redis_token_count=redis_token_count,
            summary_version=summary_version,
            ttl_seconds=ttl_seconds,
        )
    except Exception as e:
        logger.error(
            "Failed to get conversation memory snapshot",
            conversation_id=conversation_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Failed to get memory snapshot: {e!s}") from e
    else:
        return result
