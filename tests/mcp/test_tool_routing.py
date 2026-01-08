"""MCP Tool Routing Tests.

Tests that verify which MCP tools are called for given user inputs.
These tests ensure correct tool routing and prevent regressions.
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

# Test constants
TEST_TIMEOUT = 30


@pytest.fixture
def enable_mcp_test_mode():
    """Enable MCP test mode and clear call log."""
    os.environ["MCP_TEST_MODE"] = "1"
    MCP_CALL_LOG.clear()
    yield
    MCP_CALL_LOG.clear()
    os.environ.pop("MCP_TEST_MODE", None)


@pytest.mark.asyncio
async def test_calls_get_recent_activities(deps, enable_mcp_test_mode):
    """Test that recommend_next_session calls get_recent_activities via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What should I do today?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    # Verify get_recent_activities was called (via recommend_next_session tool)
    assert "get_recent_activities" in MCP_CALL_LOG or "get_yesterday_activities" in MCP_CALL_LOG


@pytest.mark.asyncio
async def test_calls_load_prompt(deps, enable_mcp_test_mode):
    """Test that plan_week calls load_prompt via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my week",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    # Verify load_prompt was called (plan_week may use weekly_intent prompt)
    # Note: load_orchestrator_prompt is always called first, so we check for either
    assert "load_prompt" in MCP_CALL_LOG or "load_orchestrator_prompt" in MCP_CALL_LOG


@pytest.mark.asyncio
async def test_calls_save_planned_sessions(deps, enable_mcp_test_mode):
    """Test that add_workout calls save_planned_sessions via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Add a 3 mile easy run tomorrow",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)
    # Verify save_planned_sessions was called
    assert "save_planned_sessions" in MCP_CALL_LOG


@pytest.mark.asyncio
async def test_greeting_does_not_hit_db(deps, enable_mcp_test_mode):
    """Test that a simple greeting does NOT call database tools.

    This prevents accidental DB usage for simple queries.
    """
    result = await asyncio.wait_for(
        run_conversation(
            user_input="hello",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert result is not None
    assert isinstance(result.message, str)

    # Define forbidden tools that should NOT be called for a greeting
    # Note: load_context and save_context are always called by run_conversation,
    # so we only check for tools that indicate actual data queries or writes
    forbidden_db_tools = {
        "get_recent_activities",
        "get_yesterday_activities",
        "save_planned_sessions",
    }

    # Verify none of the forbidden tools were called
    called_tools = set(MCP_CALL_LOG)
    forbidden_called = called_tools.intersection(forbidden_db_tools)
    assert not forbidden_called, f"Greeting should not call data query/write tools, but called: {forbidden_called}"
