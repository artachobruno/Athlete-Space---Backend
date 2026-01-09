"""Redis-based short-term conversation message store.

This module provides a Redis-backed store for normalized messages per conversation.
This is primary short-term memory, not long-term storage.

Core invariant: Redis holds only normalized, token-counted messages for a conversation,
in strict order (oldest → newest).

B26: Rolling window of normalized messages per conversation_id in Redis.
B28: Sliding window enforcement - Redis contains at most N most-recent messages per conversation.
"""

import inspect
import json

import redis
from loguru import logger

from app.config.settings import settings
from app.core.message import Message

# Maximum number of messages to keep in Redis per conversation (B28)
# This enforces a strict sliding window with oldest-turn eviction
MAX_CONVERSATION_MESSAGES = 50

# TTL for conversation keys in Redis (24 hours in seconds)
# Active conversations refresh TTL on every write
CONVERSATION_TTL_SECONDS = 86400


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


def _serialize_message(message: Message) -> str:
    """Serialize Message to JSON string.

    Args:
        message: Normalized Message object

    Returns:
        JSON string representation
    """
    return message.model_dump_json()


def _deserialize_message(message_json: str) -> Message:
    """Deserialize JSON string to Message object.

    Args:
        message_json: JSON string representation of Message

    Returns:
        Message object

    Raises:
        ValueError: If JSON is invalid or cannot be parsed into Message
    """
    try:
        data = json.loads(message_json)
        return Message(**data)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        raise ValueError(f"Failed to deserialize message: {e}") from e


def write_message(message: Message) -> None:
    """Write a normalized message to Redis.

    This function:
    1. Serializes the message to JSON
    2. Appends to Redis list (RPUSH)
    3. Trims list to MAX_CONVERSATION_MESSAGES (LTRIM) - B28 sliding window enforcement

    Redis operations are synchronous. Failures are logged but do not raise exceptions.

    Args:
        message: Normalized Message object with tokens populated
    """
    try:
        redis_client = _get_redis_client()
        key = _get_redis_key(message.conversation_id)

        # Serialize message
        message_json = _serialize_message(message)

        # Append to list (oldest → newest)
        redis_client.rpush(key, message_json)

        # B28: Enforce sliding window - trim to last N messages (oldest-first eviction)
        # Get count before trim for logging
        count_before_trim_result = redis_client.llen(key)
        # Handle potential awaitable (shouldn't happen with sync client, but type checker doesn't know)
        if inspect.isawaitable(count_before_trim_result):
            # This shouldn't happen with sync client, but satisfy type checker
            count_before_trim = 0
        else:
            count_before_trim = int(count_before_trim_result) if count_before_trim_result is not None else 0

        # LTRIM key -N -1 keeps the last N elements, preserving order (oldest → newest)
        try:
            redis_client.ltrim(key, -MAX_CONVERSATION_MESSAGES, -1)
            count_after_trim_result = redis_client.llen(key)
            # Handle potential awaitable
            if inspect.isawaitable(count_after_trim_result):
                count_after_trim = 0
            else:
                count_after_trim = int(count_after_trim_result) if count_after_trim_result is not None else 0

            # Log trim event if trimming occurred
            if count_before_trim > MAX_CONVERSATION_MESSAGES:
                logger.debug(
                    "Redis sliding window trim",
                    conversation_id=message.conversation_id,
                    max_messages=MAX_CONVERSATION_MESSAGES,
                    count_before=count_before_trim,
                    count_after=count_after_trim,
                    event="redis_sliding_window_trim",
                )
        except redis.RedisError as e:
            # LTRIM failure is non-fatal - log error but continue
            logger.warning(
                "Failed to trim Redis list (non-fatal)",
                conversation_id=message.conversation_id,
                error=str(e),
                event="redis_trim_failed",
            )

        # Refresh TTL on every write to keep active conversations alive
        # This ensures active conversations never expire mid-session
        try:
            redis_client.expire(key, CONVERSATION_TTL_SECONDS)
            logger.debug(
                "Redis TTL refreshed",
                conversation_id=message.conversation_id,
                ttl_seconds=CONVERSATION_TTL_SECONDS,
                event="redis_ttl_refreshed",
            )
        except redis.RedisError as e:
            # TTL refresh failure is non-fatal - log warning but continue
            logger.warning(
                "Failed to refresh Redis TTL (non-fatal)",
                conversation_id=message.conversation_id,
                error=str(e),
                event="redis_ttl_refresh_failed",
            )

        # Get current count for logging
        message_count = redis_client.llen(key)

        logger.debug(
            "Redis message appended",
            conversation_id=message.conversation_id,
            user_id=message.user_id,
            role=message.role,
            message_count=message_count,
            tokens=message.tokens,
            event="redis_append",
        )
    except redis.RedisError as e:
        # Redis is an optimization, not a dependency
        # Log error but continue request processing
        logger.warning(
            "Redis write failed (continuing without Redis)",
            conversation_id=message.conversation_id,
            user_id=message.user_id,
            error=str(e),
            event="redis_write_failed",
        )
    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(
            "Unexpected error writing to Redis",
            conversation_id=message.conversation_id,
            user_id=message.user_id,
            error=str(e),
            exc_info=True,
            event="redis_write_error",
        )


def get_recent_messages(conversation_id: str, limit: int = 50) -> list[Message]:
    """Get recent messages from Redis for a conversation.

    This function:
    1. Retrieves the last N messages from Redis list (LRANGE)
    2. Deserializes JSON strings to Message objects
    3. Preserves ordering (oldest → newest)

    Note: This is a read operation and does NOT refresh TTL.
    Only writes refresh TTL to reflect activity.

    If Redis is unavailable or conversation has no messages, returns empty list.

    Args:
        conversation_id: Conversation ID
        limit: Maximum number of messages to retrieve (default: 50)

    Returns:
        List of Message objects in chronological order (oldest first)
    """
    try:
        redis_client = _get_redis_client()
        key = _get_redis_key(conversation_id)

        # Get last N messages (LRANGE key -limit -1)
        # This returns the last 'limit' elements, preserving order
        message_jsons = redis_client.lrange(key, -limit, -1)
    except redis.RedisError as e:
        # Redis is an optimization, not a dependency
        # Return empty list on failure
        logger.debug(
            "Redis read failed (returning empty list)",
            conversation_id=conversation_id,
            error=str(e),
            event="redis_read_failed",
        )
        return []
    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(
            "Unexpected error reading from Redis",
            conversation_id=conversation_id,
            error=str(e),
            exc_info=True,
            event="redis_read_error",
        )
        return []
    else:
        if not message_jsons:
            logger.debug(
                "No messages found in Redis",
                conversation_id=conversation_id,
                event="redis_read_empty",
            )
            return []

        # Deserialize messages
        messages: list[Message] = []
        for message_json in message_jsons:
            try:
                message = _deserialize_message(message_json)
                messages.append(message)
            except ValueError as e:
                # Log deserialization errors but continue processing other messages
                logger.warning(
                    "Failed to deserialize message from Redis",
                    conversation_id=conversation_id,
                    error=str(e),
                    event="redis_deserialize_error",
                )
                continue

        logger.debug(
            "Retrieved messages from Redis",
            conversation_id=conversation_id,
            message_count=len(messages),
            requested_limit=limit,
            event="redis_read",
        )
        return messages


def get_message_count(conversation_id: str) -> int:
    """Get the number of messages stored in Redis for a conversation.

    Note: This is a read operation and does NOT refresh TTL.
    Only writes refresh TTL to reflect activity.

    Args:
        conversation_id: Conversation ID

    Returns:
        Number of messages in Redis (0 if conversation not found or Redis unavailable)
    """
    try:
        redis_client = _get_redis_client()
        key = _get_redis_key(conversation_id)
        count_result = redis_client.llen(key)
        # Handle potential awaitable (shouldn't happen with sync client, but type checker doesn't know)
        if inspect.isawaitable(count_result):
            # This shouldn't happen with sync client, but satisfy type checker
            return 0
        # Ensure we have an int
        if count_result is None:
            return 0
        return int(count_result)
    except (redis.RedisError, Exception) as e:
        logger.debug(
            "Failed to get message count from Redis",
            conversation_id=conversation_id,
            error=str(e),
        )
        return 0
