"""Prompt history retrieval from Redis (B30).

This module provides the centralized interface for retrieving conversation history
from Redis for prompt assembly. This is the first step where Redis memory is used.

Core invariant: Given the same conversation_id and Redis state, prompt history
retrieval must always return the same ordered message list.

This is read-only and deterministic. No side effects. No mutation. No heuristics.
"""

from loguru import logger

from app.core.memory_metrics import MemoryMetrics, log_memory_metrics
from app.core.message import Message
from app.core.redis_conversation_store import MAX_CONVERSATION_MESSAGES, get_recent_messages


def has_summary(messages: list[Message]) -> bool:
    """Check if messages contain a summary.

    Summaries are system messages with summary_version in metadata.

    Args:
        messages: List of messages to check

    Returns:
        True if a summary message is present, False otherwise
    """
    return any(msg.role == "system" and msg.metadata.get("summary_version") for msg in messages)


def extract_summary_version(messages: list[Message]) -> int | None:
    """Extract summary version from messages.

    Returns the highest summary_version found in system messages.

    Args:
        messages: List of messages to check

    Returns:
        Summary version number if found, None otherwise
    """
    versions: list[int] = []
    for msg in messages:
        if msg.role == "system":
            version = msg.metadata.get("summary_version")
            if version is not None:
                try:
                    if isinstance(version, str):
                        versions.append(int(version))
                    elif isinstance(version, int):
                        versions.append(version)
                except (ValueError, TypeError):
                    continue

    return max(versions) if versions else None


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
    except Exception:
        # Redis read failure - log error and return empty history
        logger.exception(
            f"Failed to retrieve messages from Redis (conversation_id={conversation_id})"
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

    # Calculate metrics for observability (B37)
    redis_token_count = sum(msg.tokens or 0 for msg in valid_messages)
    summary_version = extract_summary_version(valid_messages)
    summary_present = has_summary(valid_messages)

    # Log retrieval event with structured metrics
    log_memory_metrics(
        event="memory_history_loaded",
        metrics=MemoryMetrics(
            conversation_id=conversation_id,
            redis_message_count=len(valid_messages),
            redis_token_count=redis_token_count,
            prompt_token_count=None,
            summary_version=summary_version,
            summary_present=summary_present,
        ),
        extra={"roles": ",".join(roles_sequence)},
    )

    return valid_messages
