"""Tests for conversation summarization engine (B34).

Tests cover:
- Incremental update (initial mention, later goal added)
- No hallucination (thinking about future does not create facts)
- Slot override (user changes race date → summary updates)
- Merge logic (facts, preferences, goals, open_threads)
- Idempotency (same input produces same output)
"""

from datetime import UTC, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.conversation_summary import (
    ConversationSummary,
    _merge_summaries,
    get_conversation_summary,
    save_conversation_summary,
    summarize_conversation,
)
from app.core.message import Message
from app.db.models import ConversationProgress


@pytest.fixture
def sample_conversation_id() -> str:
    """Sample conversation ID for testing."""
    return "c_test-0000-0000-0000-000000000000"


@pytest.fixture
def sample_messages() -> list[Message]:
    """Sample messages for testing."""
    now = datetime.now(UTC).isoformat()
    return [
        Message(
            conversation_id="c_test-0000-0000-0000-000000000000",
            user_id="user_123",
            role="user",
            content="I'm training for a marathon on April 25, 2026",
            ts=now,
            tokens=10,
            metadata={},
        ),
        Message(
            conversation_id="c_test-0000-0000-0000-000000000000",
            user_id="user_123",
            role="assistant",
            content="Great! What's your target time?",
            ts=now,
            tokens=8,
            metadata={},
        ),
    ]


@pytest.fixture
def sample_slot_state() -> dict[str, str | int | float | bool | None]:
    """Sample slot state for testing."""
    return {
        "race_date": "2026-04-25",
        "race_distance": "marathon",
        "target_time": None,
    }


@pytest.mark.asyncio
async def test_incremental_update(sample_conversation_id: str, sample_messages: list[Message]) -> None:
    """Test that incremental updates preserve previous facts.

    Scenario:
    1. Initial marathon mention
    2. Later time goal added
    3. Summary must contain both
    """
    # First summary: marathon mention only
    initial_summary = ConversationSummary(
        facts={"race_date": "2026-04-25", "race_distance": "marathon"},
        preferences={},
        goals={"primary": "", "secondary": []},
        open_threads=[],
        last_updated=datetime.now(UTC).isoformat(),
    )

    # Second summary: target time added
    new_messages = [
        Message(
            conversation_id=sample_conversation_id,
            user_id="user_123",
            role="user",
            content="My target time is 2:25:00",
            ts=datetime.now(UTC).isoformat(),
            tokens=8,
            metadata={},
        ),
    ]

    slot_state = {
        "race_date": "2026-04-25",
        "race_distance": "marathon",
        "target_time": "02:25:00",
    }

    with patch("app.core.conversation_summary._extract_summary_via_llm") as mock_extract:
        mock_extract.return_value = ConversationSummary(
            facts={"target_time": "02:25:00"},
            preferences={},
            goals={"primary": "sub_2_25_marathon", "secondary": []},
            open_threads=[],
            last_updated=datetime.now(UTC).isoformat(),
        )

        merged = await summarize_conversation(
            conversation_id=sample_conversation_id,
            messages=new_messages,
            slot_state=slot_state,
            previous_summary=initial_summary,
        )

        # Verify merge preserves initial facts
        assert merged.facts["race_date"] == "2026-04-25"
        assert merged.facts["race_distance"] == "marathon"
        assert merged.facts["target_time"] == "02:25:00"
        assert merged.goals["primary"] == "sub_2_25_marathon"


@pytest.mark.asyncio
async def test_no_hallucination(sample_conversation_id: str) -> None:
    """Test that thinking about future does not create facts.

    Scenario:
    - Input: "I'm thinking about Boston someday"
    - Output: No race_date, no confirmed goal
    """
    messages = [
        Message(
            conversation_id=sample_conversation_id,
            user_id="user_123",
            role="user",
            content="I'm thinking about Boston someday",
            ts=datetime.now(UTC).isoformat(),
            tokens=6,
            metadata={},
        ),
    ]

    slot_state: dict[str, str | int | float | bool | None] = {}

    with patch("app.core.conversation_summary._extract_summary_via_llm") as mock_extract:
        # LLM should return empty summary (no facts extracted)
        mock_extract.return_value = ConversationSummary(
            facts={},
            preferences={},
            goals={"primary": "", "secondary": []},
            open_threads=[],
            last_updated=datetime.now(UTC).isoformat(),
        )

        summary = await summarize_conversation(
            conversation_id=sample_conversation_id,
            messages=messages,
            slot_state=slot_state,
        )

        # Verify no hallucinated facts
        assert "race_date" not in summary.facts
        assert "race_distance" not in summary.facts
        assert summary.goals["primary"] == ""
        assert len(summary.facts) == 0


@pytest.mark.asyncio
async def test_slot_override(sample_conversation_id: str) -> None:
    """Test that user changing race date updates summary.

    Scenario:
    1. Initial race date: 2026-04-25
    2. User changes to: 2026-05-10
    3. Summary must update date
    """
    # Initial summary with old date
    initial_summary = ConversationSummary(
        facts={"race_date": "2026-04-25", "race_distance": "marathon"},
        preferences={},
        goals={"primary": "sub_2_25_marathon", "secondary": []},
        open_threads=[],
        last_updated=datetime.now(UTC).isoformat(),
    )

    # User changes date
    new_messages = [
        Message(
            conversation_id=sample_conversation_id,
            user_id="user_123",
            role="user",
            content="Actually, my race is on May 10, 2026",
            ts=datetime.now(UTC).isoformat(),
            tokens=10,
            metadata={},
        ),
    ]

    slot_state = {
        "race_date": "2026-05-10",
        "race_distance": "marathon",
        "target_time": "02:25:00",
    }

    with patch("app.core.conversation_summary._extract_summary_via_llm") as mock_extract:
        mock_extract.return_value = ConversationSummary(
            facts={"race_date": "2026-05-10"},
            preferences={},
            goals={"primary": "sub_2_25_marathon", "secondary": []},
            open_threads=[],
            last_updated=datetime.now(UTC).isoformat(),
        )

        merged = await summarize_conversation(
            conversation_id=sample_conversation_id,
            messages=new_messages,
            slot_state=slot_state,
            previous_summary=initial_summary,
        )

        # Verify date is updated (overwritten, not added)
        assert merged.facts["race_date"] == "2026-05-10"
        assert merged.facts["race_distance"] == "marathon"  # Preserved
        assert merged.goals["primary"] == "sub_2_25_marathon"  # Preserved


def test_merge_summaries_facts() -> None:
    """Test that facts are merged correctly (overwrite with new values)."""
    previous = ConversationSummary(
        facts={"race_date": "2026-04-25", "race_distance": "marathon"},
        preferences={},
        goals={"primary": "", "secondary": []},
        open_threads=[],
        last_updated="2026-01-01T00:00:00Z",
    )

    new = ConversationSummary(
        facts={"target_time": "02:25:00"},
        preferences={},
        goals={"primary": "", "secondary": []},
        open_threads=[],
        last_updated="2026-01-02T00:00:00Z",
    )

    merged = _merge_summaries(previous, new)

    # Verify facts are merged (both old and new)
    assert merged.facts["race_date"] == "2026-04-25"
    assert merged.facts["race_distance"] == "marathon"
    assert merged.facts["target_time"] == "02:25:00"


def test_merge_summaries_preferences() -> None:
    """Test that preferences are merged correctly (overwrite with new values)."""
    previous = ConversationSummary(
        facts={},
        preferences={"training_style": "high mileage"},
        goals={"primary": "", "secondary": []},
        open_threads=[],
        last_updated="2026-01-01T00:00:00Z",
    )

    new = ConversationSummary(
        facts={},
        preferences={"feedback_style": "direct"},
        goals={"primary": "", "secondary": []},
        open_threads=[],
        last_updated="2026-01-02T00:00:00Z",
    )

    merged = _merge_summaries(previous, new)

    # Verify preferences are merged
    assert merged.preferences["training_style"] == "high mileage"
    assert merged.preferences["feedback_style"] == "direct"


def test_merge_summaries_goals() -> None:
    """Test that goals are merged correctly (replace if new primary, otherwise keep previous)."""
    previous = ConversationSummary(
        facts={},
        preferences={},
        goals={"primary": "sub_2_25_marathon", "secondary": ["qualify_boston"]},
        open_threads=[],
        last_updated="2026-01-01T00:00:00Z",
    )

    new = ConversationSummary(
        facts={},
        preferences={},
        goals={"primary": "sub_2_20_marathon", "secondary": ["improve_pacing"]},
        open_threads=[],
        last_updated="2026-01-02T00:00:00Z",
    )

    merged = _merge_summaries(previous, new)

    # Verify primary goal is replaced, secondary goals are merged and deduplicated
    assert merged.goals["primary"] == "sub_2_20_marathon"
    assert "qualify_boston" in merged.goals["secondary"]
    assert "improve_pacing" in merged.goals["secondary"]


def test_merge_summaries_open_threads() -> None:
    """Test that open_threads are merged correctly (deduplicated)."""
    previous = ConversationSummary(
        facts={},
        preferences={},
        goals={"primary": "", "secondary": []},
        open_threads=["weekly_plan_generation", "taper_strategy"],
        last_updated="2026-01-01T00:00:00Z",
    )

    new = ConversationSummary(
        facts={},
        preferences={},
        goals={"primary": "", "secondary": []},
        open_threads=["taper_strategy", "nutrition_plan"],
        last_updated="2026-01-02T00:00:00Z",
    )

    merged = _merge_summaries(previous, new)

    # Verify open_threads are deduplicated
    assert "weekly_plan_generation" in merged.open_threads
    assert "taper_strategy" in merged.open_threads
    assert "nutrition_plan" in merged.open_threads
    assert len(merged.open_threads) == 3


def test_merge_summaries_none_previous() -> None:
    """Test that merge with None previous summary returns new summary."""
    new = ConversationSummary(
        facts={"race_date": "2026-04-25"},
        preferences={},
        goals={"primary": "sub_2_25_marathon", "secondary": []},
        open_threads=[],
        last_updated="2026-01-02T00:00:00Z",
    )

    merged = _merge_summaries(None, new)

    # Verify new summary is returned as-is
    assert merged.facts == new.facts
    assert merged.goals == new.goals
    assert merged.open_threads == new.open_threads
    assert merged.last_updated == new.last_updated


def test_get_conversation_summary_exists(sample_conversation_id: str) -> None:
    """Test retrieving existing conversation summary from database."""
    summary_dict = {
        "facts": {"race_date": "2026-04-25"},
        "preferences": {},
        "goals": {"primary": "sub_2_25_marathon", "secondary": []},
        "open_threads": [],
        "last_updated": "2026-01-09T15:31:00Z",
    }

    with patch("app.core.conversation_summary.get_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_result = MagicMock()
        mock_progress = MagicMock()
        mock_progress.conversation_summary = summary_dict
        mock_result.first.return_value = (mock_progress,)
        mock_db.execute.return_value = mock_result

        summary = get_conversation_summary(sample_conversation_id)

        assert summary is not None
        assert summary.facts["race_date"] == "2026-04-25"
        assert summary.goals["primary"] == "sub_2_25_marathon"


def test_get_conversation_summary_not_exists(sample_conversation_id: str) -> None:
    """Test retrieving non-existent conversation summary returns None."""
    with patch("app.core.conversation_summary.get_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result

        summary = get_conversation_summary(sample_conversation_id)

        assert summary is None


def test_save_conversation_summary_new(sample_conversation_id: str) -> None:
    """Test saving new conversation summary to database."""
    summary = ConversationSummary(
        facts={"race_date": "2026-04-25"},
        preferences={},
        goals={"primary": "sub_2_25_marathon", "secondary": []},
        open_threads=[],
        last_updated="2026-01-09T15:31:00Z",
    )

    with patch("app.core.conversation_summary.get_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result

        save_conversation_summary(sample_conversation_id, summary)

        # Verify add was called (new record)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


def test_save_conversation_summary_update(sample_conversation_id: str) -> None:
    """Test updating existing conversation summary in database."""
    summary = ConversationSummary(
        facts={"race_date": "2026-05-10"},
        preferences={},
        goals={"primary": "sub_2_25_marathon", "secondary": []},
        open_threads=[],
        last_updated="2026-01-10T15:31:00Z",
    )

    with patch("app.core.conversation_summary.get_session") as mock_session:
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_result = MagicMock()
        mock_progress = MagicMock()
        mock_result.first.return_value = (mock_progress,)
        mock_db.execute.return_value = mock_result

        save_conversation_summary(sample_conversation_id, summary)

        # Verify update was called (existing record)
        assert mock_progress.conversation_summary == summary.model_dump()
        assert mock_progress.summary_updated_at is not None
        mock_db.commit.assert_called_once()
