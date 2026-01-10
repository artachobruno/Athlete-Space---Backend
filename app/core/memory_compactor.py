"""Memory compaction logic (B36).

This module implements memory compaction that replaces old Redis message history
with (latest summary + last K turns) while preserving correctness, determinism, and safety.

Core invariants:
1. Redis remains the working memory
2. Postgres summaries are immutable
3. Compaction is destructive to Redis only
4. Compaction happens only after summary is persisted
5. Reads never trigger compaction
6. Compaction is idempotent
"""

import json
from datetime import datetime, timezone

import redis
from loguru import logger

from app.config.settings import settings
from app.core.memory_config import SUMMARY_CONTEXT_TURNS, SUMMARY_SYSTEM_ROLE
from app.core.memory_metrics import increment_memory_counter
from app.core.message import Message
from app.core.redis_conversation_store import (
    CONVERSATION_TTL_SECONDS,
    MAX_CONVERSATION_MESSAGES,
    get_recent_messages,
)
from app.core.token_counting import count_tokens


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


def _render_summary_text(summary: dict) -> str:
    """Render summary dictionary as human-readable text.

    Converts structured summary (facts, preferences, goals, open_threads) into
    a readable text format for injection as a system message.

    Args:
        summary: Summary dictionary from ConversationSummary.model_dump()

    Returns:
        Rendered text representation of the summary
    """
    parts = []

    # Facts
    facts = summary.get("facts", {})
    if facts:
        parts.append("Facts:")
        for key, value in facts.items():
            parts.append(f"  - {key}: {value}")

    # Preferences
    preferences = summary.get("preferences", {})
    if preferences:
        parts.append("Preferences:")
        for key, value in preferences.items():
            parts.append(f"  - {key}: {value}")

    # Goals
    goals = summary.get("goals", {})
    primary_goal = goals.get("primary", "") if isinstance(goals, dict) else ""
    secondary_goals = goals.get("secondary", []) if isinstance(goals, dict) and isinstance(goals.get("secondary"), list) else []

    if primary_goal:
        parts.append(f"Primary Goal: {primary_goal}")

    if secondary_goals:
        parts.append("Secondary Goals:")
        parts.extend(f"  - {goal}" for goal in secondary_goals)

    # Open threads
    open_threads = summary.get("open_threads", [])
    if open_threads:
        parts.append("Open Threads:")
        parts.extend(f"  - {thread}" for thread in open_threads)

    if not parts:
        return "No summary information available."

    return "\n".join(parts)


def _extract_last_k_turns(messages: list[Message], k: int) -> list[Message]:
    """Extract last K turns from messages, ignoring existing system messages.

    A "turn" is one user message + one assistant message.
    This function:
    - Filters out existing system messages (they will be replaced)
    - Groups remaining messages into turns
    - Returns the last K turns in order

    Args:
        messages: List of messages in chronological order (oldest first)
        k: Number of turns to extract

    Returns:
        List of messages from the last K turns, in chronological order
    """
    # Filter out system messages
    real_messages = [msg for msg in messages if msg.role != SUMMARY_SYSTEM_ROLE]

    if not real_messages:
        return []

    # Group into turns: pair user messages with following assistant messages
    turns: list[list[Message]] = []
    current_turn: list[Message] = []

    for msg in real_messages:
        if msg.role == "user":
            # Start a new turn when we see a user message
            if current_turn:
                turns.append(current_turn)
            current_turn = [msg]
        elif msg.role == "assistant" and current_turn:
            # Add assistant message to current turn
            current_turn.append(msg)
            turns.append(current_turn)
            current_turn = []
        elif msg.role == "assistant":
            # Orphaned assistant message - create a turn with just this message
            turns.append([msg])
            current_turn = []

    # Add any remaining turn
    if current_turn:
        turns.append(current_turn)

    # Get last K turns
    last_turns = turns[-k:] if len(turns) > k else turns

    # Flatten turns back into message list
    result: list[Message] = []
    for turn in last_turns:
        result.extend(turn)

    return result


def compact_conversation_memory(
    *,
    conversation_id: str,
    summary: dict,
    summary_version: int,
    summary_created_at: datetime,
) -> None:
    """Replace Redis history with [system summary] + last K turns.

    This function:
    1. Reads current Redis history (read-only, no TTL refresh)
    2. Extracts last K turns (ignoring existing system messages)
    3. Builds system summary message
    4. Atomically replaces Redis list with [summary_message] + last_turns

    Compaction is idempotent - if Redis already has a system message with
    the same or higher summary_version, compaction is skipped.

    This function never raises exceptions. Failures are logged but do not
    block user requests.

    Args:
        conversation_id: Conversation ID
        summary: Summary dictionary (from ConversationSummary.model_dump())
        summary_version: Summary version number
        summary_created_at: Timestamp when summary was created
    """
    try:
        redis_client = _get_redis_client()
        key = _get_redis_key(conversation_id)

        # Step 1: Read Redis history (read-only, no TTL refresh)
        messages = get_recent_messages(conversation_id, limit=MAX_CONVERSATION_MESSAGES)

        # Safety check: Do not compact if Redis is empty
        if not messages:
            logger.info(
                "memory_compaction_skipped",
                conversation_id=conversation_id,
                summary_version=summary_version,
                reason="redis_empty",
                event="memory_compaction_skipped",
            )
            return

        # Step 2: Idempotency guard - check if already compacted with same or higher version
        first_message = messages[0]
        if first_message.role == SUMMARY_SYSTEM_ROLE:
            existing_version = first_message.metadata.get("summary_version")
            if existing_version is not None:
                try:
                    existing_version_int = int(existing_version) if isinstance(existing_version, str) else existing_version
                    if isinstance(existing_version_int, int) and existing_version_int >= summary_version:
                        logger.info(
                            "memory_compaction_skipped",
                            conversation_id=conversation_id,
                            summary_version=summary_version,
                            reason="already_compacted",
                            existing_version=existing_version_int,
                            event="memory_compaction_skipped",
                        )
                        return
                except (ValueError, TypeError) as e:
                    logger.warning(
                        "Failed to parse existing summary_version, proceeding with compaction",
                        conversation_id=conversation_id,
                        existing_version=existing_version,
                        error=str(e),
                    )

        # Step 3: Extract last K turns (ignoring existing system messages)
        last_turns = _extract_last_k_turns(messages, k=SUMMARY_CONTEXT_TURNS)

        # Step 4: Build system summary message
        rendered_text = _render_summary_text(summary)

        # Get user_id from existing messages (should be consistent across conversation)
        user_id = messages[0].user_id if messages else "unknown"

        summary_message = Message(
            conversation_id=conversation_id,
            user_id=user_id,
            role=SUMMARY_SYSTEM_ROLE,
            content=rendered_text,
            ts=summary_created_at.isoformat(),
            tokens=count_tokens(
                role=SUMMARY_SYSTEM_ROLE,
                content=rendered_text,
                conversation_id=conversation_id,
                user_id=user_id,
            ),
            metadata={
                "summary_version": str(summary_version),
                "summary_created_at": summary_created_at.isoformat(),
            },
        )

        # Step 5: Replace Redis list atomically
        messages_before = len(messages)
        compacted_messages = [summary_message, *last_turns]
        messages_after = len(compacted_messages)

        pipeline = redis_client.pipeline()
        pipeline.delete(key)
        pipeline.rpush(key, *[json.dumps(m.model_dump()) for m in compacted_messages])
        pipeline.expire(key, CONVERSATION_TTL_SECONDS)
        pipeline.execute()

        logger.info(
            "memory_compacted",
            conversation_id=conversation_id,
            summary_version=summary_version,
            messages_before=messages_before,
            messages_after=messages_after,
        )

        # Increment counter (B37)
        increment_memory_counter("compactions_run")
    except redis.RedisError as e:
        # Redis failures are non-fatal - log error but continue
        logger.warning(
            "Failed to compact memory (non-fatal)",
            conversation_id=conversation_id,
            error=str(e),
            event="memory_compaction_failed",
        )
    except Exception as e:
        # Catch-all for unexpected errors
        logger.error(
            "Unexpected error during memory compaction",
            conversation_id=conversation_id,
            error=str(e),
            exc_info=True,
            event="memory_compaction_failed",
        )
