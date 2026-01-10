"""Summarization trigger logic (B33).

This module provides a pure, deterministic function to check if conversation
summarization should be triggered based on objective thresholds.

Core invariant: Summarization is triggered only when:
1. Conversation exceeds a hard size threshold (token or message count)
2. A summary does not already cover the older history
3. Minimum messages have elapsed since last summary

No LLM calls. No Redis writes. No DB writes. Pure function.
"""

from loguru import logger

from app.core.message import Message
from app.core.summarization_config import (
    MAX_HISTORY_MESSAGES_BEFORE_SUMMARY,
    MAX_HISTORY_TOKENS_BEFORE_SUMMARY,
    MIN_MESSAGES_SINCE_LAST_SUMMARY,
)


def count_messages_since_last_summary(messages: list[Message]) -> int:
    """Count messages since the last summary message.

    Summaries are always stored as system messages with summary_version in metadata.
    This function walks messages from newest to oldest, counting until it finds
    a system message with summary_version in metadata.

    Args:
        messages: List of messages in chronological order (oldest → newest)

    Returns:
        Number of messages since last summary (0 if no summary found)
    """
    count = 0
    for msg in reversed(messages):
        if msg.role == "system" and msg.metadata.get("summary_version"):
            break
        count += 1
    return count


def should_trigger_summarization(
    *,
    history_tokens: int,
    history_message_count: int,
    messages_since_last_summary: int,
) -> bool:
    """Check if summarization should be triggered based on objective thresholds.

    This is a pure function with no side effects. It makes a deterministic
    decision based on:
    1. Total history token count
    2. Total history message count
    3. Number of messages since last summary

    Rules:
    - If messages_since_last_summary < MIN_MESSAGES_SINCE_LAST_SUMMARY, return False
    - If history_tokens >= MAX_HISTORY_TOKENS_BEFORE_SUMMARY, return True
    - If history_message_count >= MAX_HISTORY_MESSAGES_BEFORE_SUMMARY, return True
    - Otherwise, return False

    Args:
        history_tokens: Total token count of conversation history
        history_message_count: Total number of messages in history
        messages_since_last_summary: Number of messages since last summary

    Returns:
        True if summarization should be triggered, False otherwise
    """
    # Prevent summary spam - must have minimum messages since last summary
    if messages_since_last_summary < MIN_MESSAGES_SINCE_LAST_SUMMARY:
        return False

    # Token threshold - protect model safety
    # Message count threshold - protect latency + determinism
    return history_tokens >= MAX_HISTORY_TOKENS_BEFORE_SUMMARY or history_message_count >= MAX_HISTORY_MESSAGES_BEFORE_SUMMARY
