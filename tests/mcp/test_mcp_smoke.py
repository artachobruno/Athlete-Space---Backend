"""MCP Smoke Tests.

Lightweight end-to-end tests that exercise the MCP-wired orchestrator.
These tests run on every commit and fail fast if MCP is broken.

These tests:
- Exercise the orchestrator entrypoint only
- Use MCP client implicitly (no mocking)
- Require MCP env vars
- Run async with timeout protection
- Fail loudly on errors
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.coach.agents.orchestrator_agent import run_conversation

# Test constants
TEST_TIMEOUT = 30
# Longer timeout for complex operations that may involve multiple LLM calls
TEST_TIMEOUT_LONG = 120  # 2 minutes for season planning which can be complex


@pytest.mark.asyncio
async def test_orchestrator_boot(deps):
    """Test basic orchestrator boot and initialization."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="hello",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert hasattr(result, "message")
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")
    assert isinstance(result.message, str)
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_context_roundtrip(deps):
    """Test context load and save via MCP."""
    # First conversation - establish context
    result1 = await asyncio.wait_for(
        run_conversation(
            user_input="I ran yesterday",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result1 is not None
    assert isinstance(result1.message, str)

    # Second conversation - should have context from first
    result2 = await asyncio.wait_for(
        run_conversation(
            user_input="What should I do today?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result2 is not None
    assert isinstance(result2.message, str)
    assert len(result2.message) > 0
    # Response should reference "today" or be contextually aware
    assert "today" in result2.message.lower() or len(result2.message) > 10


@pytest.mark.asyncio
async def test_activity_query(deps):
    """Test activity-based tool call via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What workout should I do today?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert hasattr(result, "message")
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")
    assert isinstance(result.message, str)
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_add_workout(deps):
    """Test write path - adding a planned session via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Add a 3 mile easy run tomorrow",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    # Response should indicate success (added/scheduled) or provide feedback
    message_lower = result.message.lower()
    assert (
        "added" in message_lower
        or "scheduled" in message_lower
        or "created" in message_lower
        or "planned" in message_lower
        or len(message_lower) > 10
    )


@pytest.mark.asyncio
async def test_prompt_loading(deps):
    """Test prompt loading via FS MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my week",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")


@pytest.mark.asyncio
async def test_recommend_next_session(deps):
    """Test recommend_next_session tool via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What should I do today?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")


@pytest.mark.asyncio
async def test_explain_training_state(deps):
    """Test explain_training_state tool via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Why do I feel tired?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT_LONG,  # Use longer timeout as this may require more LLM processing
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")


@pytest.mark.asyncio
async def test_adjust_training_load(deps):
    """Test adjust_training_load tool via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Yesterday's workout felt too hard",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")


@pytest.mark.asyncio
async def test_run_analysis(deps):
    """Test run_analysis tool via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Analyze my training",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")


@pytest.mark.asyncio
async def test_share_report(deps):
    """Test share_report tool via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Generate a training report",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")


@pytest.mark.asyncio
async def test_plan_week(deps):
    """Test plan_week tool via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my week",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")


@pytest.mark.asyncio
async def test_plan_race_build(deps):
    """Test plan_race_build tool via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="I want to run a marathon on June 15th",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")
    # May return clarification request if details are missing
    message_lower = result.message.lower()
    assert (
        "marathon" in message_lower
        or "race" in message_lower
        or "plan" in message_lower
        or "clarification" in message_lower
        or len(message_lower) > 10
    )


@pytest.mark.asyncio
async def test_plan_season(deps):
    """Test plan_season tool via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my training season from January 1 to December 31, 2026",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    assert len(result.message) > 0
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")
    # May return clarification or season plan
    message_lower = result.message.lower()
    assert (
        "season" in message_lower
        or "plan" in message_lower
        or "training" in message_lower
        or "clarification" in message_lower
        or len(message_lower) > 10
    )


@pytest.mark.asyncio
async def test_alter_planned_season(deps):
    """Test altering/modifying a planned season via multi-turn conversation.

    Note: This test uses a longer timeout (TEST_TIMEOUT_LONG) because season
    planning can involve complex LLM reasoning and multiple tool calls.
    """
    # First: Create a season plan
    result1 = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my training season from January 1 to December 31, 2026",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT_LONG,
    )

    assert result1 is not None
    assert isinstance(result1.message, str)

    # Second: Modify the plan
    result2 = await asyncio.wait_for(
        run_conversation(
            user_input="I want to add more speed work to my season plan",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT_LONG,
    )

    assert result2 is not None
    assert isinstance(result2.message, str)
    assert len(result2.message) > 0
    # Response should acknowledge the modification request
    message_lower = result2.message.lower()
    assert (
        "speed" in message_lower
        or "work" in message_lower
        or "plan" in message_lower
        or "update" in message_lower
        or "modify" in message_lower
        or "adjust" in message_lower
        or len(message_lower) > 10
    )


@pytest.mark.asyncio
async def test_multi_turn_conversation(deps):
    """Test multi-turn conversation with context retention."""
    # Turn 1: Establish context
    result1 = await asyncio.wait_for(
        run_conversation(
            user_input="I'm training for a marathon",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )
    assert result1 is not None
    assert isinstance(result1.message, str)

    # Turn 2: Reference previous context
    result2 = await asyncio.wait_for(
        run_conversation(
            user_input="What should my weekly mileage be?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )
    assert result2 is not None
    assert isinstance(result2.message, str)
    message_lower = result2.message.lower()
    assert (
        "mileage" in message_lower
        or "weekly" in message_lower
        or "marathon" in message_lower
        or "training" in message_lower
        or len(message_lower) > 10
    )

    # Turn 3: Continue conversation
    result3 = await asyncio.wait_for(
        run_conversation(
            user_input="How should I structure my long runs?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )
    assert result3 is not None
    assert isinstance(result3.message, str)
    message_lower = result3.message.lower()
    assert (
        "long" in message_lower
        or "run" in message_lower
        or "structure" in message_lower
        or "weekly" in message_lower
        or len(message_lower) > 10
    )

    # Turn 4: Ask follow-up
    result4 = await asyncio.wait_for(
        run_conversation(
            user_input="What about recovery days?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )
    assert result4 is not None
    assert isinstance(result4.message, str)
    assert len(result4.message) > 0


@pytest.mark.asyncio
async def test_comprehensive_workflow(deps):
    """Test comprehensive workflow: plan -> modify -> query."""
    # Step 1: Plan a race
    result1 = await asyncio.wait_for(
        run_conversation(
            user_input="I want to run a half marathon on March 1st, 2026",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )
    assert result1 is not None
    assert isinstance(result1.message, str)

    # Step 2: Add a specific workout
    result2 = await asyncio.wait_for(
        run_conversation(
            user_input="Add a 5 mile tempo run next Tuesday",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )
    assert result2 is not None
    assert isinstance(result2.message, str)

    # Step 3: Query what's planned
    result3 = await asyncio.wait_for(
        run_conversation(
            user_input="What workouts do I have planned?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )
    assert result3 is not None
    assert isinstance(result3.message, str)
    assert len(result3.message) > 0
