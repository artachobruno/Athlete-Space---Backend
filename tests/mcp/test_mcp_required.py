"""Hard MCP Enforcement Tests.

Tests that verify the orchestrator fails without MCP configuration.
This ensures MCP can never be bypassed.
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
from app.coach.agents.orchestrator_deps import CoachDeps

# Test constants
TEST_ATHLETE_ID = 1
TEST_TIMEOUT = 30


@pytest.fixture
def deps(test_user_id: str):
    """Create CoachDeps for testing."""
    return CoachDeps(
        athlete_id=TEST_ATHLETE_ID,
        user_id=test_user_id,
        athlete_state=None,
        days=60,
        days_to_race=None,
    )


@pytest.mark.asyncio
async def test_orchestrator_fails_without_mcp_db_server(monkeypatch, deps: CoachDeps, test_user_id: str):
    """Test that orchestrator fails when MCP_DB_SERVER_URL is missing."""
    # Remove MCP_DB_SERVER_URL
    monkeypatch.delenv("MCP_DB_SERVER_URL", raising=False)
    # Keep FS server to isolate the failure
    if not os.getenv("MCP_FS_SERVER_URL"):
        monkeypatch.setenv("MCP_FS_SERVER_URL", "http://localhost:8081")

    # The orchestrator should fail when trying to load context or call DB tools
    with pytest.raises((RuntimeError, Exception)):
        await asyncio.wait_for(
            run_conversation(
                user_input="hello",
                deps=deps,
            ),
            timeout=TEST_TIMEOUT,
        )


@pytest.mark.asyncio
async def test_orchestrator_fails_without_mcp_fs_server(monkeypatch, deps: CoachDeps, test_user_id: str):
    """Test that orchestrator fails when MCP_FS_SERVER_URL is missing."""
    # Remove MCP_FS_SERVER_URL
    monkeypatch.delenv("MCP_FS_SERVER_URL", raising=False)
    # Keep DB server to isolate the failure
    if not os.getenv("MCP_DB_SERVER_URL"):
        monkeypatch.setenv("MCP_DB_SERVER_URL", "http://localhost:8080")

    # The orchestrator should fail when trying to load orchestrator prompt
    with pytest.raises((RuntimeError, FileNotFoundError, Exception)):
        await asyncio.wait_for(
            run_conversation(
                user_input="hello",
                deps=deps,
            ),
            timeout=TEST_TIMEOUT,
        )


@pytest.mark.asyncio
async def test_orchestrator_fails_without_both_mcp_servers(monkeypatch, deps: CoachDeps, test_user_id: str):
    """Test that orchestrator fails when both MCP servers are missing."""
    # Remove both MCP server URLs
    monkeypatch.delenv("MCP_DB_SERVER_URL", raising=False)
    monkeypatch.delenv("MCP_FS_SERVER_URL", raising=False)

    # The orchestrator should fail immediately
    with pytest.raises((RuntimeError, FileNotFoundError, Exception)):
        await asyncio.wait_for(
            run_conversation(
                user_input="hello",
                deps=deps,
            ),
            timeout=TEST_TIMEOUT,
        )
