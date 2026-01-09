"""Prompt builder with history injection (B31).

This module assembles the final LLM input prompt by combining:
- system instructions
- retrieved conversation history (B30)
- the current user message

in a deterministic, debuggable, and safe way.

Core invariant: Given the same system prompt, history, and user input,
the built LLM prompt must always be identical.

No hidden state. No mutation. No heuristics.
"""

from typing import Literal, TypedDict

from loguru import logger

from app.core.message import Message
from app.core.prompt_history import get_prompt_history
from app.core.token_counting import count_tokens


class LLMMessage(TypedDict):
    """LLM message format with role and content.

    This matches the format expected by pydantic_ai and OpenAI API.
    Role values match the canonical Message.role type for consistency.
    """

    role: Literal["user", "assistant", "system"]
    content: str


# Soft ceiling for prompt size (warning only, no truncation in B31)
# This is a very high threshold - actual enforcement comes in B32
SOFT_PROMPT_SIZE_CEILING = 200000  # tokens


def build_prompt(
    conversation_id: str,
    current_user_message: Message,
    system_prompt: str,
) -> list[LLMMessage]:
    """Build the final LLM input prompt by combining system, history, and current message.

    This is the ONLY place prompts are assembled. No endpoint should build prompts inline.

    Structure (in order):
    1. System prompt (first message, role="system")
    2. Conversation history (ordered oldest → newest)
    3. Current user message (last message, role="user")

    Args:
        conversation_id: Conversation ID in format c_<UUID>
        current_user_message: Current user message (normalized Message)
        system_prompt: System prompt text to use as first message

    Returns:
        List of LLM messages in format [{"role": "system|user|assistant", "content": "..."}]
        Ordered: system, history (oldest→newest), current user message

    Raises:
        ValueError: If current_user_message is invalid or system_prompt is empty
    """
    # Validate inputs
    if not system_prompt or not system_prompt.strip():
        raise ValueError("system_prompt cannot be empty")

    if current_user_message.conversation_id != conversation_id:
        raise ValueError(
            f"current_user_message.conversation_id ({current_user_message.conversation_id}) "
            f"does not match conversation_id ({conversation_id})"
        )

    if current_user_message.role != "user":
        raise ValueError(f"current_user_message must have role='user', got role='{current_user_message.role}'")

    # Step 1: System prompt (first message)
    system_message: LLMMessage = {
        "role": "system",
        "content": system_prompt.strip(),
    }

    # Step 2: Retrieve conversation history (B30)
    # This returns ordered Messages (oldest → newest)
    history_messages = get_prompt_history(conversation_id)

    # Step 3: Convert canonical Messages → LLM messages
    # Preserve ordering, preserve role exactly
    # Map Message.role → LLMMessage.role
    # Map Message.content → LLMMessage.content
    # Ignore metadata, timestamps, tokens
    llm_history: list[LLMMessage] = [
        {
            "role": msg.role,  # Already validated as "user"|"assistant"|"system"
            "content": msg.content,
        }
        for msg in history_messages
    ]

    # Step 4: Append current user message last
    current_llm_message: LLMMessage = {
        "role": current_user_message.role,
        "content": current_user_message.content,
    }

    # Assemble final prompt
    prompt: list[LLMMessage] = [system_message, *llm_history, current_llm_message]

    # Step 5: Token counting and soft size guard
    total_tokens = _count_prompt_tokens(prompt, conversation_id, current_user_message.user_id)

    # Step 6: Logging & observability
    roles_sequence = [msg["role"] for msg in prompt]
    logger.info(
        "Prompt built",
        conversation_id=conversation_id,
        history_count=len(history_messages),
        total_tokens=total_tokens,
        roles_sequence=roles_sequence,
        event="prompt_built",
    )

    # Soft size guard (warning only, no truncation in B31)
    if total_tokens > SOFT_PROMPT_SIZE_CEILING:
        logger.warning(
            "Prompt size exceeds soft ceiling",
            conversation_id=conversation_id,
            total_tokens=total_tokens,
            ceiling=SOFT_PROMPT_SIZE_CEILING,
            event="prompt_size_warning",
        )

    return prompt


def _count_prompt_tokens(
    prompt: list[LLMMessage],
    conversation_id: str,
    user_id: str,
) -> int:
    """Count total tokens in assembled prompt.

    Args:
        prompt: List of LLM messages
        conversation_id: Conversation ID for logging
        user_id: User ID for logging

    Returns:
        Total token count across all messages
    """
    total = 0
    for msg in prompt:
        role_str = msg["role"]
        # Validate and convert to proper literal type
        if role_str not in {"user", "assistant", "system"}:
            logger.warning(
                "Invalid role in prompt message, skipping token count",
                conversation_id=conversation_id,
                role=role_str,
            )
            continue
        role: Literal["user", "assistant", "system"] = role_str
        content = msg["content"]
        # Use the same token counting function as message normalization
        token_count = count_tokens(
            role=role,
            content=content,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        total += token_count

    return total
