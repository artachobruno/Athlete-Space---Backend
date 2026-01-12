"""Conversation health collector (read-only).

Tracks conversation metrics and summarization health.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import func, select

from app.core.memory_metrics import MEMORY_COUNTERS
from app.db.models import ConversationMessage, ConversationSummary
from app.db.session import get_session
from app.internal.ai_ops.types import ConversationStats


def collect_conversation_health() -> ConversationStats:
    """Collect conversation health metrics.

    Returns:
        ConversationStats with avg turns, summaries per conversation, compression ratio
    """
    try:
        with get_session() as db:
            # Get all conversations (via ConversationMessage)
            messages = db.execute(select(ConversationMessage)).scalars().all()

            if not messages:
                return ConversationStats(
                    avg_turns=0.0,
                    summaries_per_conversation=0.0,
                    compression_ratio=0.0,
                )

            # Group messages by conversation
            conversation_message_counts: dict[str, int] = {}
            for message in messages:
                conversation_id = message.conversation_id
                conversation_message_counts[conversation_id] = (
                    conversation_message_counts.get(conversation_id, 0) + 1
                )

            # Calculate average turns (messages per conversation / 2, since a turn = user + assistant)
            total_messages = len(messages)
            unique_conversations = len(conversation_message_counts)
            avg_turns = (total_messages / unique_conversations / 2.0) if unique_conversations > 0 else 0.0

            # Get summaries
            summaries = db.execute(select(ConversationSummary)).scalars().all()

            # Count summaries per conversation
            conversation_summary_counts: dict[str, int] = {}
            for summary in summaries:
                conversation_id = summary.conversation_id
                conversation_summary_counts[conversation_id] = (
                    conversation_summary_counts.get(conversation_id, 0) + 1
                )

            # Calculate summaries per conversation
            total_summaries = len(summaries)
            summaries_per_conversation = (
                total_summaries / unique_conversations if unique_conversations > 0 else 0.0
            )

            # Calculate compression ratio
            # This is approximate: summaries_created / compactions_run from memory counters
            summaries_created = MEMORY_COUNTERS.get("summaries_created", 0)
            compactions_run = MEMORY_COUNTERS.get("compactions_run", 0)

            if compactions_run > 0:
                compression_ratio = summaries_created / compactions_run
            else:
                compression_ratio = 0.0

            return ConversationStats(
                avg_turns=avg_turns,
                summaries_per_conversation=summaries_per_conversation,
                compression_ratio=compression_ratio,
            )

    except Exception as e:
        logger.warning(f"Failed to collect conversation health: {e}")
        return ConversationStats(
            avg_turns=0.0,
            summaries_per_conversation=0.0,
            compression_ratio=0.0,
        )
