"""Summarization queue interface (B33 placeholder, B34 implementation).

This module provides the interface for enqueueing conversation summarization tasks.
B33 implements the trigger logic and calls this function when summarization is needed.

B34 will implement the actual summarization generation logic.

Core invariant: This function never blocks. It enqueues work asynchronously.
"""

from loguru import logger


def enqueue_conversation_summary(conversation_id: str) -> None:
    """Enqueue a conversation summarization task.

    This function is called by B33 when summarization trigger fires.
    It schedules summarization to run asynchronously without blocking the request.

    B34 will implement the actual summarization logic:
    - Generate summary via LLM
    - Store summary as system message with summary_version in metadata
    - Compact conversation history

    For now, this is a placeholder that only logs the event.
    The actual implementation will be in B34.

    Args:
        conversation_id: Conversation ID to summarize
    """
    # Placeholder implementation for B33
    # B34 will implement actual summarization logic
    logger.debug(
        "Conversation summarization enqueued (B34 placeholder)",
        conversation_id=conversation_id,
        event="summary_enqueued",
    )
