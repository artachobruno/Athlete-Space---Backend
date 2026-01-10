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

from app.core.memory_metrics import MemoryMetrics, log_memory_metrics
from app.core.message import Message
from app.core.prompt_history import extract_summary_version, get_prompt_history, has_summary
from app.core.summarization_config import (
    MAX_HISTORY_MESSAGES_BEFORE_SUMMARY,
    MAX_HISTORY_TOKENS_BEFORE_SUMMARY,
)
from app.core.summarization_queue import enqueue_conversation_summary
from app.core.summarization_trigger import (
    count_messages_since_last_summary,
    should_trigger_summarization,
)
from app.core.token_counting import count_tokens
from app.core.token_guard import enforce_token_limit


class LLMMessage(TypedDict):
    """LLM message format with role and content.

    This matches the format expected by pydantic_ai and OpenAI API.
    Role values match the canonical Message.role type for consistency.
    """

    role: Literal["user", "assistant", "system"]
    content: str


# Token guard is now enforced in build_prompt (B32)


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

    # Step 2.5: Check summarization trigger (B33)
    # This happens after history retrieval but before prompt building
    # Trigger is checked based on objective thresholds (tokens, messages)
    if history_messages:
        # Calculate history metrics
        history_tokens = sum(msg.tokens for msg in history_messages)
        history_message_count = len(history_messages)
        messages_since_last_summary = count_messages_since_last_summary(history_messages)

        # Check if summarization should be triggered
        if should_trigger_summarization(
            history_tokens=history_tokens,
            history_message_count=history_message_count,
            messages_since_last_summary=messages_since_last_summary,
        ):
            # Determine trigger reason
            reason = "unknown"
            if history_tokens >= MAX_HISTORY_TOKENS_BEFORE_SUMMARY:
                reason = "token_threshold"
            elif history_message_count >= MAX_HISTORY_MESSAGES_BEFORE_SUMMARY:
                reason = "message_threshold"

            logger.info(
                "summarization_triggered",
                conversation_id=conversation_id,
                reason=reason,
                history_tokens=history_tokens,
                history_messages=history_message_count,
                messages_since_last_summary=messages_since_last_summary,
            )

            # Enqueue summarization asynchronously (non-blocking)
            # B34 will implement the actual summarization logic
            enqueue_conversation_summary(conversation_id)

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

    # Step 5: Enforce token limit with deterministic truncation (B32)
    # This ensures no LLM call can exceed token limits
    prompt, truncation_meta = enforce_token_limit(
        prompt,
        conversation_id=conversation_id,
        user_id=current_user_message.user_id,
    )

    # Step 6: Logging & observability (B37)
    roles_sequence = [msg["role"] for msg in prompt]
    history_tokens = sum(msg.tokens or 0 for msg in history_messages)
    summary_version = extract_summary_version(history_messages)
    summary_present = has_summary(history_messages)

    log_memory_metrics(
        event="prompt_built",
        metrics=MemoryMetrics(
            conversation_id=conversation_id,
            redis_message_count=len(history_messages),
            redis_token_count=history_tokens,
            prompt_token_count=truncation_meta["final_tokens"],
            summary_version=summary_version,
            summary_present=summary_present,
        ),
        extra={
            "roles_sequence": ",".join(roles_sequence),
            "truncated": truncation_meta["truncated"],
            "removed_count": truncation_meta.get("removed_count", 0),
            "original_tokens": truncation_meta.get("original_tokens", truncation_meta["final_tokens"]),
        },
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
