"""Token counting for messages.

This module provides centralized token counting functionality using tiktoken
for OpenAI models. All token counting must go through this module to ensure
consistency and determinism.

Core invariant: Every message has a token count before storage or prompt use.
"""

from typing import Literal

import tiktoken
from loguru import logger

# Hard safety ceiling - reject messages exceeding this
MAX_TOKENS_PER_MESSAGE = 50000

# Expected token norms for warning logs
WARNING_TOKEN_THRESHOLD = 10000  # Warn if message exceeds this


def _get_encoding() -> tiktoken.Encoding:
    """Get tiktoken encoding for OpenAI models.

    Uses cl100k_base encoding which is used by:
    - gpt-4
    - gpt-3.5-turbo
    - gpt-4o
    - gpt-4o-mini

    Returns:
        tiktoken.Encoding instance
    """
    return tiktoken.get_encoding("cl100k_base")


def _format_message_for_counting(role: Literal["user", "assistant", "system"], content: str) -> str:
    r"""Format message for token counting.

    This matches the format used in OpenAI API calls:
    - System messages: "system\n{content}"
    - User messages: "user\n{content}"
    - Assistant messages: "assistant\n{content}"

    Args:
        role: Message role
        content: Message content

    Returns:
        Formatted string for token counting
    """
    return f"{role}\n{content}"


def count_tokens(
    role: Literal["user", "assistant", "system"],
    content: str,
    conversation_id: str,
    user_id: str,
) -> int:
    """Count tokens for a normalized message.

    This is a pure, deterministic function that:
    - Counts tokens based on role and content
    - Uses the same formatting as OpenAI API calls
    - Always returns the same count for the same input

    Args:
        role: Message role
        content: Message content
        conversation_id: Conversation ID for logging
        user_id: User ID for logging

    Returns:
        Token count as integer

    Raises:
        ValueError: If token count exceeds MAX_TOKENS_PER_MESSAGE
    """
    encoding = _get_encoding()
    formatted = _format_message_for_counting(role, content)
    token_count = len(encoding.encode(formatted))

    # Defensive limit check
    if token_count > MAX_TOKENS_PER_MESSAGE:
        logger.error(
            "Message token count exceeds safety ceiling",
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            token_count=token_count,
            max_tokens=MAX_TOKENS_PER_MESSAGE,
            content_length=len(content),
        )
        raise ValueError(f"Message token count ({token_count}) exceeds maximum allowed ({MAX_TOKENS_PER_MESSAGE})")

    # Warning for unusually large messages
    if token_count > WARNING_TOKEN_THRESHOLD:
        logger.warning(
            "Message token count exceeds expected norm",
            conversation_id=conversation_id,
            user_id=user_id,
            role=role,
            token_count=token_count,
            threshold=WARNING_TOKEN_THRESHOLD,
            content_length=len(content),
        )

    # Debug logging for all messages
    logger.debug(
        "Counted tokens for message",
        conversation_id=conversation_id,
        user_id=user_id,
        role=role,
        token_count=token_count,
        content_length=len(content),
    )

    return token_count
