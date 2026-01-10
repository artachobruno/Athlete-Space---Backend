"""Hard token guard with deterministic truncation (B32).

This module enforces token limits on LLM prompts by truncating conversation
history deterministically. The guard ensures:

1. System prompt is NEVER truncated
2. Current user message is NEVER truncated
3. History is truncated from oldest messages first
4. Truncation is deterministic (same input → same output)
5. No LLM call can exceed token limits

Core invariant: prompt_tokens + completion_tokens < MAX_MODEL_TOKENS
"""

from typing import Literal, TypedDict

from loguru import logger

from app.core.memory_metrics import increment_memory_counter
from app.core.token_counting import (
    MAX_MODEL_TOKENS,
    MAX_PROMPT_TOKENS,
    count_tokens,
)


class LLMMessage(TypedDict):
    """LLM message format with role and content.

    This matches the format expected by pydantic_ai and OpenAI API.
    """

    role: Literal["user", "assistant", "system"]
    content: str


class TruncationMetadata(TypedDict):
    """Metadata about truncation operation."""

    truncated: bool
    removed_count: int
    final_tokens: int
    original_tokens: int


def count_prompt_tokens(
    messages: list[LLMMessage],
    conversation_id: str,
    user_id: str,
) -> int:
    """Count total tokens in a list of LLM messages.

    This is a pure, deterministic function that counts tokens for each message
    and sums them. Uses the same token counting logic as message normalization.

    Args:
        messages: List of LLM messages
        conversation_id: Conversation ID for logging
        user_id: User ID for logging

    Returns:
        Total token count as integer
    """
    total = 0
    for msg in messages:
        role_str = msg["role"]
        # Validate and convert to proper literal type
        if role_str not in {"user", "assistant", "system"}:
            logger.warning(
                "Invalid role in message, skipping token count",
                conversation_id=conversation_id,
                role=role_str,
            )
            continue
        role: Literal["user", "assistant", "system"] = role_str
        content = msg["content"]
        token_count = count_tokens(
            role=role,
            content=content,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        total += token_count

    return total


def enforce_token_limit(
    messages: list[LLMMessage],
    conversation_id: str,
    user_id: str,
    max_prompt_tokens: int = MAX_PROMPT_TOKENS,
) -> tuple[list[LLMMessage], TruncationMetadata]:
    """Enforce token limit on LLM messages with deterministic truncation.

    This function ensures no LLM call can exceed token limits by truncating
    conversation history from the oldest messages first. The system prompt
    and current user message are always preserved.

    Algorithm:
    1. Count total tokens in all messages
    2. If within limit, return messages unchanged
    3. If over limit:
       a. Split messages: system (first), user_tail (last), history (middle)
       b. Walk history from newest → oldest
       c. Add messages to truncated_history until limit would be exceeded
       d. Drop remaining oldest messages
    4. Verify final prompt doesn't exceed model hard limit
    5. Return truncated messages and metadata

    Args:
        messages: List of LLM messages in order [system, history..., user]
        conversation_id: Conversation ID for logging
        user_id: User ID for logging
        max_prompt_tokens: Maximum allowed prompt tokens (default: MAX_PROMPT_TOKENS)

    Returns:
        Tuple of:
        - Truncated messages (guaranteed to be within limit)
        - Metadata dict with truncation information

    Raises:
        RuntimeError: If prompt exceeds model hard limit even after truncation
                     (should never happen, but exists as safety check)
    """
    # Count original tokens
    original_tokens = count_prompt_tokens(messages, conversation_id, user_id)

    # If within limit, return unchanged
    if original_tokens <= max_prompt_tokens:
        logger.info(
            "token_guard",
            conversation_id=conversation_id,
            user_id=user_id,
            truncated=False,
            removed_messages=0,
            original_tokens=original_tokens,
            final_tokens=original_tokens,
            event="token_guard",
        )
        return messages, {
            "truncated": False,
            "removed_count": 0,
            "final_tokens": original_tokens,
            "original_tokens": original_tokens,
        }

    # Validate message structure
    if not messages:
        raise ValueError("messages cannot be empty")

    # Split messages: system (first), user_tail (last), history (middle)
    system = messages[0]
    if system["role"] != "system":
        raise ValueError("First message must be system message")

    user_tail = messages[-1]
    if user_tail["role"] != "user":
        raise ValueError("Last message must be user message")

    history = messages[1:-1]

    # Walk history from newest → oldest, building truncated_history
    truncated_history: list[LLMMessage] = []
    removed: list[LLMMessage] = []

    # Iterate history in reverse (newest first)
    for msg in reversed(history):
        # Try adding this message
        test_history = [msg, *truncated_history]
        test_messages = [system, *test_history, user_tail]
        test_tokens = count_prompt_tokens(test_messages, conversation_id, user_id)

        if test_tokens <= max_prompt_tokens:
            # This message fits, add it to truncated_history
            truncated_history.insert(0, msg)
        else:
            # This message would exceed limit, drop it
            removed.append(msg)

    # Build final messages
    final_messages = [system, *truncated_history, user_tail]
    final_tokens = count_prompt_tokens(final_messages, conversation_id, user_id)

    # Absolute hard failure check - should never happen
    if final_tokens > MAX_MODEL_TOKENS:
        logger.error(
            "Prompt exceeds model hard token limit after truncation",
            conversation_id=conversation_id,
            user_id=user_id,
            final_tokens=final_tokens,
            max_model_tokens=MAX_MODEL_TOKENS,
            system_tokens=count_prompt_tokens([system], conversation_id, user_id),
            user_tail_tokens=count_prompt_tokens([user_tail], conversation_id, user_id),
        )
        raise RuntimeError(f"Prompt exceeds model hard token limit ({final_tokens} > {MAX_MODEL_TOKENS})")

    # Log truncation event (B37)
    logger.info(
        "token_guard",
        conversation_id=conversation_id,
        user_id=user_id,
        truncated=True,
        removed_messages=len(removed),
        original_tokens=original_tokens,
        final_tokens=final_tokens,
        event="token_guard",
    )

    # Increment truncation counter (B37)
    increment_memory_counter("token_truncations")

    return final_messages, {
        "truncated": True,
        "removed_count": len(removed),
        "final_tokens": final_tokens,
        "original_tokens": original_tokens,
    }
