"""Prompt history retrieval from Redis (B30).

This module provides the centralized interface for retrieving conversation history
from Redis for prompt assembly. This is the first step where Redis memory is used.

Core invariant: Given the same conversation_id and Redis state, prompt history
retrieval must always return the same ordered message list.

This is read-only and deterministic. No side effects. No mutation. No heuristics.
"""

from loguru import logger

from app.core.message import Message
from app.core.redis_conversation_store import MAX_CONVERSATION_MESSAGES, get_recent_messages


def get_prompt_history(conversation_id: str, limit: int | None = None) -> list[Message]:
    """Retrieve recent conversation turns from Redis in correct role order.

    This is the ONLY way prompt history is retrieved. No endpoint or prompt builder
    should read Redis directly.

    This function:
    1. Reads messages from Redis (read-only, no mutation)
    2. Preserves strict ordering (oldest → newest)
    3. Validates message integrity (role, content)
    4. Enforces limit defensively
    5. Returns canonical Message objects

    Args:
        conversation_id: Conversation ID in format c_<UUID>
        limit: Maximum number of messages to retrieve (default: MAX_CONVERSATION_MESSAGES)

    Returns:
        Ordered list of canonical Message objects (oldest → newest)
        Roles preserved exactly (system, user, assistant)
        Empty list on failure (system degrades to stateless behavior)

    Raises:
        None - all errors are handled gracefully, returns empty list
    """
    # Use default limit if not provided
    if limit is None:
        limit = MAX_CONVERSATION_MESSAGES

    # Validate limit is positive
    if limit <= 0:
        logger.warning(
            "Invalid limit provided to get_prompt_history",
            conversation_id=conversation_id,
            limit=limit,
            event="invalid_limit",
        )
        return []

    # Enforce maximum limit defensively
    effective_limit = min(limit, MAX_CONVERSATION_MESSAGES)

    # Read messages from Redis (read-only, no mutation)
    try:
        raw_messages = get_recent_messages(conversation_id, effective_limit)
    except Exception as e:
        # Redis read failure - log error and return empty history
        logger.error(
            "Failed to retrieve messages from Redis",
            conversation_id=conversation_id,
            error=str(e),
            exc_info=True,
            event="prompt_history_retrieval_failed",
        )
        return []

    # Validate message integrity and filter invalid messages
    valid_messages: list[Message] = []
    for message in raw_messages:
        # Validate role
        if message.role not in {"system", "user", "assistant"}:
            logger.warning(
                "Invalid role in retrieved message, dropping",
                conversation_id=conversation_id,
                message_role=message.role,
                message_ts=message.ts,
                event="invalid_message_role",
            )
            continue

        # Validate content is non-empty
        if not message.content or not message.content.strip():
            logger.warning(
                "Empty content in retrieved message, dropping",
                conversation_id=conversation_id,
                message_role=message.role,
                message_ts=message.ts,
                event="empty_message_content",
            )
            continue

        # Message is valid - add to list
        valid_messages.append(message)

    # Enforce limit again at retrieval time (defensive check)
    if len(valid_messages) > effective_limit:
        logger.warning(
            "Message count exceeds limit, truncating",
            conversation_id=conversation_id,
            message_count=len(valid_messages),
            limit=effective_limit,
            event="message_limit_exceeded",
        )
        valid_messages = valid_messages[:effective_limit]

    # Extract roles sequence for logging (debug only)
    roles_sequence = [msg.role for msg in valid_messages]

    # Log retrieval event
    logger.debug(
        "Prompt history retrieved",
        conversation_id=conversation_id,
        message_count=len(valid_messages),
        roles=roles_sequence,
        event="prompt_history_retrieved",
    )

    return valid_messages
