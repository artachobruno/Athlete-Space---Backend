"""Conversation summarization engine (B34).

This module implements deterministic, structured conversation summarization
that converts chat history into long-term memory: facts, preferences, goals, and open threads.

This is NOT chat summarization - it is state extraction + compression.

Core invariants:
1. Conversation context (full history + slot_state) is the source of truth
2. Structured output only - no prose, no paragraphs, no narrative summaries
3. Idempotent - running twice on the same input produces the same output
4. No hallucination - only extract facts explicitly stated or confirmed
"""

import json
from datetime import datetime, timezone
from typing import Literal, NoReturn

import redis
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.coach.config.models import USER_FACING_MODEL
from app.config.settings import settings
from app.core.memory_compactor import compact_conversation_memory
from app.core.memory_metrics import increment_memory_counter
from app.core.message import Message
from app.core.redis_conversation_store import get_recent_messages
from app.core.token_counting import count_tokens
from app.db.models import (
    ConversationMessage,
    ConversationProgress,
)
from app.db.models import (
    ConversationSummary as ConversationSummaryModel,
)
from app.db.session import get_session
from app.services.llm.model import get_model


class ConversationSummary(BaseModel):
    """Structured conversation summary schema.

    This is the ONLY allowed output format for conversation summarization.
    All fields must be present (empty dicts/lists, not omitted).

    Fields:
        facts: Dictionary of factual information (e.g., race_date, race_distance, target_time)
        preferences: Dictionary of user preferences (e.g., training_style, feedback_style)
        goals: Dictionary with primary goal and secondary goals list
        open_threads: List of open conversation threads/topics
        last_updated: ISO-8601 timestamp of last update
    """

    facts: dict[str, str] = Field(default_factory=dict, description="Factual information extracted from conversation")
    preferences: dict[str, str] = Field(default_factory=dict, description="User preferences extracted from conversation")
    goals: dict[str, str | list[str]] = Field(
        default_factory=lambda: {"primary": "", "secondary": []},
        description="Goals extracted from conversation",
    )
    open_threads: list[str] = Field(default_factory=list, description="Open conversation threads/topics")
    last_updated: str = Field(..., description="ISO-8601 timestamp of last update (UTC)")


def _get_extraction_prompt() -> str:
    """Get the LLM prompt for structured state extraction.

    Returns:
        System prompt for the extraction agent
    """
    return """You are a state extraction engine.
Your job is to extract ONLY factual information from conversations.
Return VALID JSON matching the schema exactly.
Do not infer.
Do not explain.
Do not include text outside JSON.
Do not add facts that are not explicitly stated or confirmed.
Only extract information from the provided messages and slot state."""


def _get_extraction_user_prompt(
    recent_messages: list[Message],
    slot_state: dict[str, str | int | float | bool | None],
    previous_summary: ConversationSummary | None,
) -> str:
    """Build user prompt for LLM extraction.

    Args:
        recent_messages: Recent messages since last summary (or all if no previous summary)
        slot_state: Current conversation slot state
        previous_summary: Previous summary if exists

    Returns:
        Formatted user prompt string
    """
    prompt_parts = []

    # Add previous summary if exists (for incremental update)
    if previous_summary:
        prompt_parts.append("Previous Summary:")
        prompt_parts.append(json.dumps(previous_summary.model_dump(), indent=2))
        prompt_parts.append("")

    # Add recent messages
    prompt_parts.append("Recent Messages (since last summary):")
    prompt_parts.extend(f"{msg.role.upper()}: {msg.content}" for msg in recent_messages)
    prompt_parts.append("")

    # Add slot state
    if slot_state:
        prompt_parts.append("Current Slot State:")
        prompt_parts.append(json.dumps(slot_state, indent=2, default=str))
        prompt_parts.append("")

    prompt_parts.append(
        "Extract facts, preferences, goals, and open threads from the above. "
        "Return JSON matching the ConversationSummary schema. "
        "Only include facts explicitly stated or confirmed. Do not infer."
    )

    return "\n".join(prompt_parts)


def _validate_summary_type(summary: ConversationSummary | object) -> None:
    """Validate that summary is a ConversationSummary instance.

    Args:
        summary: Summary object to validate

    Raises:
        TypeError: If summary is not a ConversationSummary instance
    """
    if not isinstance(summary, ConversationSummary):
        raise TypeError(f"LLM returned invalid summary type: {type(summary)}")


async def _extract_summary_via_llm(
    conversation_id: str,
    recent_messages: list[Message],
    slot_state: dict[str, str | int | float | bool | None],
    previous_summary: ConversationSummary | None,
) -> ConversationSummary:
    """Extract structured summary using LLM.

    Args:
        conversation_id: Conversation ID
        recent_messages: Recent messages since last summary
        slot_state: Current conversation slot state
        previous_summary: Previous summary if exists (for incremental update)

    Returns:
        Extracted ConversationSummary

    Raises:
        ValueError: If LLM extraction fails or returns invalid JSON
        RuntimeError: If LLM call fails
    """
    if not recent_messages and not slot_state:
        # No new information to extract
        logger.debug("No messages or slot state to extract from")
        if previous_summary:
            return previous_summary
        # Return empty summary with current timestamp
        now = datetime.now(timezone.utc).isoformat()
        return ConversationSummary(
            facts={},
            preferences={},
            goals={"primary": "", "secondary": []},
            open_threads=[],
            last_updated=now,
        )

    try:
        model = get_model("openai", USER_FACING_MODEL)
        system_prompt = _get_extraction_prompt()
        user_prompt = _get_extraction_user_prompt(recent_messages, slot_state, previous_summary)

        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            output_type=ConversationSummary,
        )

        logger.info(
            "Calling LLM for conversation summary extraction",
            message_count=len(recent_messages),
            has_slot_state=bool(slot_state),
            has_previous_summary=previous_summary is not None,
        )
        logger.debug(
            "LLM Prompt: Conversation Summary Extraction",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        result = await agent.run(user_prompt)
        summary = result.output

        # Validate summary structure
        _validate_summary_type(summary)

        # Ensure all required fields are present
        if "facts" not in summary.model_dump():
            summary.facts = {}
        if "preferences" not in summary.model_dump():
            summary.preferences = {}
        if "goals" not in summary.model_dump():
            summary.goals = {"primary": "", "secondary": []}
        if "open_threads" not in summary.model_dump():
            summary.open_threads = []

        # Set last_updated to current timestamp
        summary.last_updated = datetime.now(timezone.utc).isoformat()

        # Count tokens in summary (for observability)
        summary_content = json.dumps(summary.model_dump())
        summary_tokens = count_tokens(
            role="system",
            content=summary_content,
            conversation_id=conversation_id,
            user_id="system",  # Summary is system-generated
        )

        logger.info(
            "summary_generated",
            conversation_id=conversation_id,
            summary_version=None,  # Version assigned during persistence
            summary_tokens=summary_tokens,
            facts_count=len(summary.facts),
            preferences_count=len(summary.preferences),
            open_threads_count=len(summary.open_threads),
        )
    except Exception as e:
        logger.exception(
            f"Failed to extract conversation summary via LLM (message_count={len(recent_messages)})"
        )
        raise RuntimeError(f"Failed to extract conversation summary: {e!s}") from e
    else:
        return summary


def _merge_summaries(
    previous_summary: ConversationSummary | None,
    new_summary: ConversationSummary,
) -> ConversationSummary:
    """Merge previous summary with new summary (incremental update).

    Merge rules:
    1. facts: Update (overwrite) with new facts
    2. preferences: Update (overwrite) with new preferences
    3. goals: Replace if new goals provided, otherwise keep previous
    4. open_threads: Deduplicate and merge lists
    5. last_updated: Use new timestamp

    Args:
        previous_summary: Previous summary (None if first summary)
        new_summary: Newly extracted summary

    Returns:
        Merged ConversationSummary
    """
    if previous_summary is None:
        return new_summary

    # Merge facts (overwrite with new values)
    merged_facts = previous_summary.facts.copy()
    merged_facts.update(new_summary.facts)

    # Merge preferences (overwrite with new values)
    merged_preferences = previous_summary.preferences.copy()
    merged_preferences.update(new_summary.preferences)

    # Merge goals (replace if new primary goal, otherwise keep previous)
    merged_goals: dict[str, str | list[str]] = {"primary": "", "secondary": []}
    if new_summary.goals.get("primary"):
        merged_goals["primary"] = new_summary.goals["primary"]
    elif previous_summary.goals.get("primary"):
        merged_goals["primary"] = previous_summary.goals["primary"]

    # Merge secondary goals (deduplicate)
    previous_secondary = previous_summary.goals.get("secondary", [])
    new_secondary = new_summary.goals.get("secondary", [])
    if not isinstance(previous_secondary, list):
        previous_secondary = []
    if not isinstance(new_secondary, list):
        new_secondary = []
    merged_secondary = list(set(previous_secondary + new_secondary))

    merged_goals["secondary"] = merged_secondary

    # Merge open_threads (deduplicate)
    merged_threads = list(set(previous_summary.open_threads + new_summary.open_threads))

    return ConversationSummary(
        facts=merged_facts,
        preferences=merged_preferences,
        goals=merged_goals,
        open_threads=merged_threads,
        last_updated=new_summary.last_updated,
    )


def _get_messages_since_last_summary(
    conversation_id: str,
    last_summary_timestamp: datetime | None,
) -> list[Message]:
    """Get messages since last summary timestamp.

    Args:
        conversation_id: Conversation ID
        last_summary_timestamp: Timestamp of last summary (None if no previous summary)

    Returns:
        List of messages since last summary, in chronological order
    """
    # Try Redis first (fast path)
    messages = get_recent_messages(conversation_id, limit=100)

    # If Redis is empty, fall back to database
    if not messages:
        # Convert c_<UUID> format to UUID for database query if needed
        # Database may store as UUID type, so strip the 'c_' prefix
        db_conversation_id = conversation_id
        if conversation_id.startswith("c_"):
            db_conversation_id = conversation_id[2:]  # Strip 'c_' prefix

        with get_session() as db:
            query = select(ConversationMessage).where(ConversationMessage.conversation_id == db_conversation_id)
            if last_summary_timestamp:
                query = query.where(ConversationMessage.ts > last_summary_timestamp)
            query = query.order_by(ConversationMessage.ts).limit(100)
            db_messages = db.execute(query).scalars().all()
            messages = []
            for db_msg in db_messages:
                try:
                    # Convert ConversationMessage to Message format
                    # Validate and cast role to literal type
                    role_str = db_msg.role
                    if role_str not in {"user", "assistant", "system"}:
                        logger.warning(
                            "Invalid role in ConversationMessage, skipping",
                            conversation_id=conversation_id,
                            message_id=db_msg.id,
                            role=role_str,
                        )
                        continue

                    # Validate required fields before creating Message
                    if not db_msg.user_id:
                        logger.warning(
                            "Missing user_id in ConversationMessage, skipping",
                            conversation_id=conversation_id,
                            message_id=db_msg.id,
                        )
                        continue

                    if db_msg.ts is None:
                        logger.warning(
                            "Missing ts in ConversationMessage, skipping",
                            conversation_id=conversation_id,
                            message_id=db_msg.id,
                        )
                        continue

                    if db_msg.tokens is None:
                        logger.warning(
                            "Missing tokens in ConversationMessage, skipping",
                            conversation_id=conversation_id,
                            message_id=db_msg.id,
                        )
                        continue

                    role: Literal["user", "assistant", "system"] = role_str  # type: ignore[assignment]

                    # Convert ts to ISO format string
                    ts_str = db_msg.ts.isoformat() if isinstance(db_msg.ts, datetime) else str(db_msg.ts)

                    msg = Message(
                        conversation_id=db_msg.conversation_id,
                        user_id=db_msg.user_id,
                        role=role,
                        content=db_msg.content,
                        ts=ts_str,
                        tokens=db_msg.tokens,
                        metadata=db_msg.message_metadata or {},
                    )
                    messages.append(msg)
                except Exception as e:
                    logger.debug(
                        "Failed to convert ConversationMessage to Message",
                        conversation_id=conversation_id,
                        message_id=db_msg.id,
                        error=str(e),
                        exc_info=True,
                    )
                    continue

    # Filter by timestamp if we have a last summary timestamp
    if last_summary_timestamp:
        return [msg for msg in messages if datetime.fromisoformat(msg.ts.replace("Z", "+00:00")) > last_summary_timestamp]

    return messages


# ============================================================================
# B35: Versioned Summary Storage (Postgres + Redis Cache)
# ============================================================================

# TTL for summary cache in Redis (7-30 days, using 14 days as default)
SUMMARY_CACHE_TTL_SECONDS = 14 * 24 * 60 * 60  # 14 days


def _get_redis_client() -> redis.Redis:
    """Get Redis client instance for summary caching.

    Returns:
        Redis client with string decoding enabled
    """
    return redis.from_url(settings.redis_url, decode_responses=True)


def _get_summary_redis_key(conversation_id: str) -> str:
    """Construct Redis key for latest conversation summary.

    Args:
        conversation_id: Conversation ID

    Returns:
        Redis key string
    """
    return f"conversation:{conversation_id}:summary"


def get_next_summary_version(db: Session, conversation_id: str) -> int:
    """Get the next version number for a conversation summary.

    Versions are monotonically increasing per conversation, starting at 1.
    This function queries Postgres (source of truth) to determine the next version.

    Args:
        db: SQLAlchemy database session
        conversation_id: Conversation ID (format: c_<UUID> or <UUID>)

    Returns:
        Next version number (1 if no previous summaries exist)
    """
    # Convert c_<UUID> format to UUID for database query if needed
    # Database may store as UUID type, so strip the 'c_' prefix
    db_conversation_id = conversation_id
    if conversation_id.startswith("c_"):
        db_conversation_id = conversation_id[2:]  # Strip 'c_' prefix

    last_version = db.execute(
        select(func.max(ConversationSummaryModel.version)).where(ConversationSummaryModel.conversation_id == db_conversation_id)
    ).scalar()
    return (last_version or 0) + 1


def persist_conversation_summary(
    *,
    conversation_id: str,
    summary: dict,
) -> None:
    """Persist conversation summary to Postgres (append-only, versioned).

    This function never raises exceptions. Postgres is the source of truth.
    Failures are logged but do not block user requests.

    Args:
        conversation_id: Conversation ID
        summary: Summary dictionary (from ConversationSummary.model_dump())
    """
    def _raise_persistence_error() -> NoReturn:
        """Raise error for persistence failure."""
        raise ValueError("Failed to persist conversation summary after retries")

    try:
        # Convert c_<UUID> format to UUID for database if needed
        # Database stores conversation_id as UUID type, so strip the 'c_' prefix
        db_conversation_id = conversation_id
        if conversation_id.startswith("c_"):
            db_conversation_id = conversation_id[2:]  # Strip 'c_' prefix

        # Retry logic to handle race conditions
        max_retries = 3
        version = None
        created_at = None

        for attempt in range(max_retries):
            try:
                with get_session() as db:
                    version = get_next_summary_version(db, conversation_id)
                    created_at = datetime.now(timezone.utc)

                    row = ConversationSummaryModel(
                        conversation_id=db_conversation_id,
                        version=version,
                        summary=summary,
                        created_at=created_at,
                    )
                    db.add(row)
                    db.commit()
                    break  # Success, exit retry loop
            except Exception as e:
                # Check if it's a unique constraint violation (race condition)
                error_str = str(e).lower()
                is_unique_violation = (
                    "unique" in error_str
                    or "duplicate" in error_str
                    or "uniqueconstraint" in error_str
                    or "integrityerror" in error_str
                )

                if is_unique_violation and attempt < max_retries - 1:
                    # Race condition detected - retry with fresh version lookup
                    logger.warning(
                        f"Race condition detected in conversation summary persistence (attempt {attempt + 1}/{max_retries}), retrying...",
                        conversation_id=conversation_id,
                    )
                    continue
                # Either not a race condition or max retries reached - re-raise
                raise

        # Continue with rest of function after successful commit
        if version is None or created_at is None:
            _raise_persistence_error()

        logger.info(
            "summary_persisted",
            conversation_id=conversation_id,
            summary_version=version,
            storage="postgres+redis",
        )

        # Increment counter
        increment_memory_counter("summaries_created")

        # Cache the latest summary in Redis after successful persistence
        cache_latest_summary(
            conversation_id=conversation_id,
            version=version,
            summary=summary,
            created_at=created_at,
        )

        # Compact Redis memory after successful persistence and caching (B36)
        compact_conversation_memory(
            conversation_id=conversation_id,
            summary=summary,
            summary_version=version,
            summary_created_at=created_at,
        )
    except Exception:
        logger.exception(
            "Failed to persist conversation summary",
            extra={"conversation_id": conversation_id},
        )


def cache_latest_summary(
    *,
    conversation_id: str,
    version: int,
    summary: dict,
    created_at: datetime,
) -> None:
    """Cache the latest summary in Redis (write-through pattern).

    This function never raises exceptions. Redis failures are non-fatal.
    Only called after successful Postgres write.

    Args:
        conversation_id: Conversation ID
        version: Summary version number
        summary: Summary dictionary
        created_at: Creation timestamp
    """
    try:
        redis_client = _get_redis_client()
        key = _get_summary_redis_key(conversation_id)

        cache_value = {
            "version": version,
            "summary": summary,
            "created_at": created_at.isoformat(),
        }

        redis_client.set(
            key,
            json.dumps(cache_value),
            ex=SUMMARY_CACHE_TTL_SECONDS,
        )

        logger.info(
            "Cached latest conversation summary",
            conversation_id=conversation_id,
            version=version,
            event="summary_cached",
        )
    except redis.RedisError as e:
        # Redis failures are non-fatal - log warning but continue
        logger.warning(
            "Failed to cache summary in Redis (non-fatal)",
            conversation_id=conversation_id,
            error=str(e),
        )
    except Exception as e:
        # Catch-all for unexpected errors
        logger.warning(
            "Unexpected error caching summary in Redis",
            conversation_id=conversation_id,
            error=str(e),
        )


def get_latest_conversation_summary(
    conversation_id: str,
) -> dict | None:
    """Get the latest conversation summary (with cache-healing).

    Retrieval path:
    1. Try Redis cache (fast path)
    2. Fallback to Postgres (query latest version)
    3. Backfill Redis cache if Postgres has data

    This function never mutates state. Reads are deterministic.

    Args:
        conversation_id: Conversation ID

    Returns:
        Dictionary with keys: version, summary, created_at (ISO string)
        None if no summary exists
    """
    # 1. Try Redis cache (fast path)
    try:
        redis_client = _get_redis_client()
        key = _get_summary_redis_key(conversation_id)
        cached = redis_client.get(key)
        if cached and isinstance(cached, str):
            try:
                return json.loads(cached)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse cached summary JSON",
                    conversation_id=conversation_id,
                    error=str(e),
                )
    except redis.RedisError:
        # Redis read failure - continue to Postgres fallback
        pass
    except Exception as e:
        logger.debug(
            "Unexpected error reading from Redis cache",
            conversation_id=conversation_id,
            error=str(e),
        )

    # 2. Fallback to Postgres (source of truth)
    logger.debug(
        "Summary cache miss, querying Postgres",
        conversation_id=conversation_id,
        event="summary_cache_miss",
    )

    try:
        # Convert c_<UUID> format to UUID for database query if needed
        # Database may store as UUID type, so strip the 'c_' prefix
        db_conversation_id = conversation_id
        if conversation_id.startswith("c_"):
            db_conversation_id = conversation_id[2:]  # Strip 'c_' prefix

        with get_session() as db:
            row = (
                db.query(ConversationSummaryModel)
                .filter(ConversationSummaryModel.conversation_id == db_conversation_id)
                .order_by(ConversationSummaryModel.version.desc())
                .first()
            )

        if not row:
            return None

        result = {
            "version": row.version,
            "summary": row.summary,
            "created_at": row.created_at.isoformat() if isinstance(row.created_at, datetime) else str(row.created_at),
        }

        # 3. Backfill Redis cache (cache-healing)
        try:
            created_at_parsed = row.created_at if isinstance(row.created_at, datetime) else datetime.fromisoformat(result["created_at"])
            cache_latest_summary(
                conversation_id=conversation_id,
                version=row.version,
                summary=row.summary,
                created_at=created_at_parsed,
            )
        except Exception as e:
            # Cache backfill failure is non-fatal - log but return result
            logger.debug(
                "Failed to backfill Redis cache (non-fatal)",
                conversation_id=conversation_id,
                error=str(e),
            )
    except Exception:
        logger.exception(
            f"Failed to retrieve summary from Postgres (conversation_id={conversation_id})"
        )
        return None
    else:
        return result


def get_conversation_summary(conversation_id: str) -> ConversationSummary | None:
    """Get existing conversation summary from versioned storage (B35).

    Uses the latest version from Postgres (with Redis cache optimization).
    Converts the stored dict to ConversationSummary Pydantic model.

    Args:
        conversation_id: Conversation ID

    Returns:
        ConversationSummary if exists, None otherwise
    """
    latest = get_latest_conversation_summary(conversation_id)
    if not latest:
        return None

    try:
        summary_dict = latest.get("summary")
        if isinstance(summary_dict, dict):
            return ConversationSummary(**summary_dict)
    except Exception as e:
        logger.warning(
            "Failed to parse conversation summary from storage",
            conversation_id=conversation_id,
            error=str(e),
        )
        return None
    return None


async def summarize_conversation(
    conversation_id: str,
    messages: list[Message] | None = None,
    slot_state: dict[str, str | int | float | bool | None] | None = None,
    previous_summary: ConversationSummary | None = None,
) -> ConversationSummary:
    """Summarize a conversation into structured long-term memory.

    This is the main entry point for conversation summarization.

    Args:
        conversation_id: Conversation ID
        messages: Optional list of messages (if None, retrieved from database/Redis)
        slot_state: Optional slot state (if None, retrieved from ConversationProgress)
        previous_summary: Optional previous summary (if None, retrieved from database)

    Returns:
        ConversationSummary with merged facts, preferences, goals, and open_threads

    Raises:
        ValueError: If conversation_id is invalid
        RuntimeError: If summarization fails
    """
    if not conversation_id or not conversation_id.startswith("c_"):
        raise ValueError(f"Invalid conversation_id: {conversation_id}")

    # Load previous summary if not provided
    if previous_summary is None:
        previous_summary = get_conversation_summary(conversation_id)

    # Load slot_state if not provided
    if slot_state is None:
        with get_session() as db:
            result = db.execute(select(ConversationProgress).where(ConversationProgress.conversation_id == conversation_id)).first()
            if result:
                progress = result[0]
                slot_state = progress.slots or {}
            else:
                slot_state = {}

    # Get last summary timestamp
    last_summary_timestamp: datetime | None = None
    if previous_summary and previous_summary.last_updated:
        try:
            last_summary_timestamp = datetime.fromisoformat(previous_summary.last_updated.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "Failed to parse last_updated timestamp from previous summary",
                conversation_id=conversation_id,
                last_updated=previous_summary.last_updated,
            )

    # Get messages since last summary (or all if no previous summary)
    if messages is None:
        messages = _get_messages_since_last_summary(conversation_id, last_summary_timestamp)
    elif last_summary_timestamp:
        # Filter provided messages by timestamp
        messages = [msg for msg in messages if datetime.fromisoformat(msg.ts.replace("Z", "+00:00")) > last_summary_timestamp]

    # Ensure slot_state is not None (required by LLM prompt builder)
    if slot_state is None:
        slot_state = {}

    logger.info(
        "Summarizing conversation",
        conversation_id=conversation_id,
        message_count=len(messages),
        has_slot_state=bool(slot_state),
        has_previous_summary=previous_summary is not None,
    )

    # Extract new summary via LLM
    new_summary = await _extract_summary_via_llm(conversation_id, messages, slot_state, previous_summary)

    # Merge with previous summary (incremental update)
    return _merge_summaries(previous_summary, new_summary)


def save_conversation_summary(
    conversation_id: str,
    summary: ConversationSummary,
) -> None:
    """Save conversation summary to versioned storage (B35).

    Persists summary to Postgres (append-only, versioned) and caches in Redis.
    This function never raises exceptions. Failures are logged but do not block.

    Args:
        conversation_id: Conversation ID
        summary: ConversationSummary to save
    """
    if not conversation_id or not conversation_id.startswith("c_"):
        logger.warning(
            "Invalid conversation_id provided to save_conversation_summary",
            conversation_id=conversation_id,
        )
        return

    summary_dict = summary.model_dump()

    # Persist to Postgres (append-only, versioned)
    # This also handles Redis caching after successful persistence
    persist_conversation_summary(
        conversation_id=conversation_id,
        summary=summary_dict,
    )
