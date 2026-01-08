"""MCP Tool Routing Tests.

Tests that verify MCP integration works correctly for various user queries.
These tests use invariant-based assertions to remain stable across LLM routing changes.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Add project root to path
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.coach.agents.orchestrator_agent import run_conversation
from app.coach.mcp_client import MCP_CALL_LOG

# Mark all tests in this file as integration tests
pytestmark = pytest.mark.integration

# Test constants
TEST_TIMEOUT = 30
TEST_TIMEOUT_LONG = 120  # For long-running operations like season planning


def assert_valid_response(result):
    """Assert that a response has the required structure and valid values."""
    assert result is not None
    assert hasattr(result, "message")
    assert hasattr(result, "intent")
    assert hasattr(result, "response_type")

    assert isinstance(result.message, str)
    assert isinstance(result.intent, str)
    assert isinstance(result.response_type, str)

    assert len(result.message.strip()) > 0
    assert result.response_type in {
        "tool",
        "conversation",
        "clarification",
        "error",
    }


@pytest.fixture
def enable_mcp_test_mode():
    """Enable MCP test mode and clear call log."""
    os.environ["MCP_TEST_MODE"] = "1"
    MCP_CALL_LOG.clear()
    yield
    MCP_CALL_LOG.clear()
    os.environ.pop("MCP_TEST_MODE", None)


@pytest.mark.asyncio
async def test_mcp_is_used_when_expected(deps, enable_mcp_test_mode):
    """Test that MCP is actually used for queries that require it.

    This is the single explicit routing test that verifies MCP wiring works.
    """
    queries = [
        "What should I do today?",
        "Generate a training report",
        "Add a 5 mile run tomorrow",
    ]

    for query in queries:
        MCP_CALL_LOG.clear()
        result = await asyncio.wait_for(
            run_conversation(user_input=query, deps=deps),
            timeout=TEST_TIMEOUT,
        )

        assert_valid_response(result)
        assert len(MCP_CALL_LOG) > 0, f"No MCP calls for query: {query}"


@pytest.mark.asyncio
async def test_daily_recommendation(deps, enable_mcp_test_mode):
    """Test daily workout recommendation query."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What should I do today?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Verify MCP was used (invariant: any MCP call means wiring works)
    assert len(MCP_CALL_LOG) > 0
    # Content sanity check only (not exact wording)
    assert len(result.message) > 20


@pytest.mark.asyncio
async def test_weekly_planning(deps, enable_mcp_test_mode):
    """Test weekly planning query."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my week",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0
    # Content sanity check
    assert len(result.message) > 20


@pytest.mark.asyncio
async def test_add_workout(deps, enable_mcp_test_mode):
    """Test adding a workout to the plan."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Add a 3 mile easy run tomorrow",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0
    # Content sanity check
    assert len(result.message) > 20


@pytest.mark.asyncio
async def test_generate_report(deps, enable_mcp_test_mode):
    """Test generating a training report."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Generate a training report",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0
    # Content sanity check (reports should be substantial)
    assert len(result.message) > 20


@pytest.mark.asyncio
async def test_plan_season(deps, enable_mcp_test_mode):
    """Test season planning (long-running operation)."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my training season from January 1 to December 31, 2026",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT_LONG,
    )

    assert_valid_response(result)
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0
    # Season plans should be substantial
    assert len(result.message) > 50


@pytest.mark.asyncio
async def test_clarification_allowed(deps, enable_mcp_test_mode):
    """Test that clarification responses are handled correctly.

    This allows all correct outcomes (clarification, tool, or conversation).
    """
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my race",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Clarification is a valid response type
    assert result.response_type in {"clarification", "tool", "conversation"}
    # Verify MCP was used (even for clarifications, context is loaded)
    assert len(MCP_CALL_LOG) > 0


@pytest.mark.asyncio
async def test_greeting_does_not_hit_db(deps, enable_mcp_test_mode):
    """Test that a simple greeting does NOT call expensive database tools.

    This prevents accidental DB usage for simple queries.
    Note: load_context and save_context are always called by run_conversation,
    so we only check for tools that indicate actual data queries or writes.
    """
    result = await asyncio.wait_for(
        run_conversation(
            user_input="hello",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)

    # Define forbidden tools that should NOT be called for a greeting
    # These are expensive operations that shouldn't happen for simple greetings
    forbidden_db_tools = {
        "get_recent_activities",
        "get_yesterday_activities",
        "save_planned_sessions",
    }

    # Verify none of the forbidden tools were called
    called_tools = set(MCP_CALL_LOG)
    forbidden_called = called_tools.intersection(forbidden_db_tools)
    assert not forbidden_called, f"Greeting should not call data query/write tools, but called: {forbidden_called}"
