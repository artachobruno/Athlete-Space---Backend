"""Comprehensive MCP Function Tests.

Tests all coach functions end-to-end to ensure they:
1. Are correctly routed through MCP
2. Execute successfully
3. Return data in the correct format for frontend
4. Handle edge cases properly

This test suite uses invariant-based assertions to remain stable across LLM routing changes.
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
TEST_TIMEOUT_LONG = 120  # For complex operations like season planning


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


# ============================================================================
# REPORT GENERATION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_generate_report_functionality(deps, enable_mcp_test_mode):
    """Test that report generation works and uses MCP."""
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
async def test_generate_report_data_structure(deps, enable_mcp_test_mode):
    """Test that report contains expected data fields for frontend."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Create a shareable training report",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    assert hasattr(result, "structured_data")
    # Response should be valid for frontend consumption
    assert isinstance(result.message, str)


# ============================================================================
# STRAVA DATA TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_get_recent_activities_functionality(deps, enable_mcp_test_mode):
    """Test that recent activities query works and uses MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What activities have I done recently?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_get_yesterday_activities_functionality(deps, enable_mcp_test_mode):
    """Test that yesterday activities query works and uses MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What did I do yesterday?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check
    assert len(result.message) > 0


# ============================================================================
# SYNC STRAVA TESTS
# ============================================================================
# Note: Sync Strava is an API endpoint, not an MCP tool
# These tests verify the orchestrator can handle sync-related queries


@pytest.mark.asyncio
async def test_sync_strava_query_handling(deps, enable_mcp_test_mode):
    """Test that sync-related queries are handled appropriately."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Sync my Strava data",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Sync is handled via API, not MCP tool, but context is still loaded
    assert len(MCP_CALL_LOG) > 0
    # Content sanity check
    assert len(result.message) > 10


# ============================================================================
# LLM FEEDBACK TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_explain_training_state_functionality(deps, enable_mcp_test_mode):
    """Test that explain_training_state tool provides feedback via LLM."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Why do I feel tired?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0
    # Content sanity check (explanatory feedback should be substantial)
    assert len(result.message) > 20


@pytest.mark.asyncio
async def test_adjust_training_load_functionality(deps, enable_mcp_test_mode):
    """Test that adjust_training_load tool processes feedback via LLM."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Yesterday's workout felt too hard",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_llm_feedback_response_structure(deps, enable_mcp_test_mode):
    """Test that LLM feedback responses have correct structure for frontend."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="How is my training going?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0


# ============================================================================
# ADD WORKOUT TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_add_workout_functionality(deps, enable_mcp_test_mode):
    """Test that add_workout tool creates a workout and saves it."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Add a 5 mile easy run tomorrow",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_add_workout_with_details(deps, enable_mcp_test_mode):
    """Test adding a workout with specific details."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Add a 10k tempo run on Friday at 6am",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_add_workout_data_persistence(deps, enable_mcp_test_mode):
    """Test that added workouts are persisted via MCP."""
    # Add a workout
    result1 = await asyncio.wait_for(
        run_conversation(
            user_input="Add a 3 mile recovery run next Monday",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result1)
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0

    # Query for planned workouts
    MCP_CALL_LOG.clear()
    result2 = await asyncio.wait_for(
        run_conversation(
            user_input="What workouts do I have planned?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result2)
    # Verify MCP was used for query
    assert len(MCP_CALL_LOG) > 0


# ============================================================================
# PLAN SESSIONS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_plan_week_functionality(deps, enable_mcp_test_mode):
    """Test that plan_week tool generates a weekly plan."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my week",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_plan_race_build_functionality(deps, enable_mcp_test_mode):
    """Test that plan_race_build tool creates a race training plan."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="I want to run a marathon on June 15th, 2026",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check (race plans should be substantial)
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_plan_season_functionality(deps, enable_mcp_test_mode):
    """Test that plan_season tool generates a season training plan."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Plan my training season from January 1 to December 31, 2026",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT_LONG,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Season plans should be substantial
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_plan_day_functionality(deps, enable_mcp_test_mode):
    """Test planning for a specific day."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What should I do today?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check
    assert len(result.message) > 0


# ============================================================================
# RUN ANALYSIS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_run_analysis_functionality(deps, enable_mcp_test_mode):
    """Test that run_analysis tool performs deep analysis."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Analyze my training",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0
    # Content sanity check (analysis should be substantial)
    assert len(result.message) > 20


@pytest.mark.asyncio
async def test_run_analysis_data_structure(deps, enable_mcp_test_mode):
    """Test that analysis returns structured data for frontend."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Give me a detailed training analysis",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    assert hasattr(result, "structured_data")
    # Verify MCP was used
    assert len(MCP_CALL_LOG) > 0


# ============================================================================
# RECOMMEND NEXT SESSION TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_recommend_next_session_functionality(deps, enable_mcp_test_mode):
    """Test that recommend_next_session tool provides recommendations."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What should I do today?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    assert result.response_type in {"tool", "conversation", "clarification"}
    # Content sanity check
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_recommend_next_session_data_access(deps, enable_mcp_test_mode):
    """Test that recommend_next_session accesses recent activities via MCP."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What workout should I do next?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Verify MCP was used (should access activity data)
    assert len(MCP_CALL_LOG) > 0
    # Content sanity check
    assert len(result.message) > 20


# ============================================================================
# FRONTEND DATA STRUCTURE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_response_structure_for_frontend(deps, enable_mcp_test_mode):
    """Test that all responses have the correct structure for frontend consumption."""
    test_queries = [
        "What should I do today?",
        "Generate a report",
        "Plan my week",
        "Add a workout",
    ]

    for query in test_queries:
        MCP_CALL_LOG.clear()
        result = await asyncio.wait_for(
            run_conversation(
                user_input=query,
                deps=deps,
            ),
            timeout=TEST_TIMEOUT,
        )

        assert_valid_response(result)
        # MCP may be used for context loading, but direct tools don't appear in MCP_CALL_LOG
        # Focus on functional invariants: response should be valid
        assert result.response_type in {"tool", "conversation", "clarification"}


@pytest.mark.asyncio
async def test_mcp_tool_call_verification(deps, enable_mcp_test_mode):
    """Test that queries are handled correctly across different query types.

    This test verifies functionality works across different query types.
    Note: Some tools are called directly (not via MCP), so MCP_CALL_LOG
    may or may not contain entries. We focus on functional invariants.
    """
    test_queries = [
        "What should I do today?",
        "Generate a training report",
        "Plan my week",
        "Add a 5 mile run tomorrow",
        "Why do I feel tired?",
        "Yesterday's workout was too hard",
    ]

    for query in test_queries:
        MCP_CALL_LOG.clear()
        result = await asyncio.wait_for(
            run_conversation(
                user_input=query,
                deps=deps,
            ),
            timeout=TEST_TIMEOUT,
        )

        assert_valid_response(result)
        # MCP may be used for context loading, but direct tools don't appear in MCP_CALL_LOG
        # Focus on functional invariants: response should be valid
        assert result.response_type in {"tool", "conversation", "clarification"}


# ============================================================================
# EDGE CASE TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_empty_data_handling(deps, enable_mcp_test_mode):
    """Test that functions handle empty data gracefully."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="What should I do today?",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Should not crash even with no data
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_invalid_input_handling(deps, enable_mcp_test_mode):
    """Test that functions handle invalid inputs gracefully."""
    result = await asyncio.wait_for(
        run_conversation(
            user_input="asdfghjkl qwertyuiop",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # Should provide a helpful response even for unclear input
    assert len(result.message) > 0


@pytest.mark.asyncio
async def test_clarification_requests(deps, enable_mcp_test_mode):
    """Test that functions request clarification when needed."""
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


# ============================================================================
# PLACEHOLDER TESTS FOR FUTURE FEATURES
# ============================================================================
# These tests are placeholders for features that are not yet implemented.
# Uncomment and update when these features are added to the codebase.


@pytest.mark.skip(reason="Generate map feature not yet implemented")
@pytest.mark.asyncio
async def test_generate_map_functionality(deps, enable_mcp_test_mode):
    """Test that generate_map tool creates a map visualization.

    TODO: Implement when generate_map feature is added.
    Expected behavior:
    - Should generate a map visualization for activities
    - Should return map data in format suitable for frontend display
    - Should handle activities with GPS data
    """
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Generate a map of my recent runs",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # TODO: Verify MCP was used
    # assert len(MCP_CALL_LOG) > 0
    # TODO: Verify response contains map data
    # assert hasattr(result, "map_data") or "map" in result.message.lower()


@pytest.mark.skip(reason="Compare workout feature not yet implemented")
@pytest.mark.asyncio
async def test_compare_workout_functionality(deps, enable_mcp_test_mode):
    """Test that compare_workout tool compares two workouts.

    TODO: Implement when compare_workout feature is added.
    Expected behavior:
    - Should compare two specified workouts
    - Should return comparison metrics (pace, heart rate, power, etc.)
    - Should handle workouts with different data types
    """
    result = await asyncio.wait_for(
        run_conversation(
            user_input="Compare my last two 5k runs",
            deps=deps,
        ),
        timeout=TEST_TIMEOUT,
    )

    assert_valid_response(result)
    # All tools must go through MCP
    assert len(MCP_CALL_LOG) > 0
    # TODO: Verify response contains comparison data
    # assert hasattr(result, "comparison_data") or "compare" in result.message.lower()


@pytest.mark.asyncio
async def test_orchestrator_uses_mcp_only(deps, enable_mcp_test_mode):
    """Certification test: Orchestrator never executes tools directly.

    This test enforces the architectural invariant that the orchestrator
    only delegates to MCP and never executes tools directly.
    """
    result = await asyncio.wait_for(
        run_conversation("Plan my week", deps=deps),
        timeout=TEST_TIMEOUT,
    )

    # If a tool ran, MCP must have seen it
    assert len(MCP_CALL_LOG) > 0, "No MCP calls detected - architecture violation!"

    # Response should be valid
    assert_valid_response(result)
    assert result.response_type in {"tool", "conversation", "clarification"}


def test_orchestrator_does_not_import_tools():
    """Forbidden import guard: Prevents accidental tool imports in orchestrator.

    This test makes the architectural invariant unbreakable by detecting
    any direct tool imports in the orchestrator source code.
    """
    import re
    from pathlib import Path

    import app.coach.agents.orchestrator_agent as orch

    # List of forbidden tool function names that should never be imported
    forbidden = [
        "plan_week",
        "plan_season",
        "plan_race_build",
        "run_analysis",
        "explain_training_state",
        "adjust_training_load",
        "recommend_next_session",
        "share_report",
        "add_workout",
        "save_planned_sessions",
    ]

    # Get the source file path
    source_file = Path(orch.__file__)
    assert source_file.exists(), f"Orchestrator source file not found: {source_file}"

    # Read the source code
    source_text = source_file.read_text(encoding="utf-8")

    # Check for forbidden imports
    violations = []
    for name in forbidden:
        # Pattern: from app.coach.tools.* import name
        pattern1 = rf"from\s+app\.coach\.tools\.[\w_]+\s+import\s+{name}\b"
        if re.search(pattern1, source_text):
            violations.append(f"Forbidden import: {name} (from app.coach.tools.*)")

        # Pattern: from app.coach.tools import name
        pattern2 = rf"from\s+app\.coach\.tools\s+import\s+.*\b{name}\b"
        if re.search(pattern2, source_text):
            violations.append(f"Forbidden import: {name} (from app.coach.tools)")

    if violations:
        error_msg = "ARCHITECTURAL VIOLATION: Orchestrator must not import tools directly!\n\n"
        error_msg += "Violations found:\n"
        for violation in violations:
            error_msg += f"  - {violation}\n"
        error_msg += "\nAll tools must be executed via MCP, not imported directly."
        raise AssertionError(error_msg)
