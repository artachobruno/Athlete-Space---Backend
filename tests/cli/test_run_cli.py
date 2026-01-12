"""Tests for run_cli with local database and filesystem.

These tests verify that the CLI works correctly with local MCP servers
(DB server on port 8080, FS server on port 8081) that use local database
and filesystem.
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

from cli.cli import ClientConfig, _run_client_async, _validate_mcp_servers


@pytest.fixture(scope="session", autouse=True)
def require_local_mcp_env():
    """Require local MCP environment variables to be set.

    This fixture ensures that MCP_DB_SERVER_URL and MCP_FS_SERVER_URL
    are set to localhost URLs (the default for local testing).
    Uses settings defaults if environment variables are not explicitly set.
    Skips all tests if MCP servers are not configured.
    """
    # Import here to avoid circular imports
    from app.config.settings import settings

    # Use settings defaults if env vars not set
    db_url = os.getenv("MCP_DB_SERVER_URL") or settings.mcp_db_server_url
    fs_url = os.getenv("MCP_FS_SERVER_URL") or settings.mcp_fs_server_url

    # Set environment variables so they're available to the CLI
    if not os.getenv("MCP_DB_SERVER_URL"):
        os.environ["MCP_DB_SERVER_URL"] = db_url or ""
    if not os.getenv("MCP_FS_SERVER_URL"):
        os.environ["MCP_FS_SERVER_URL"] = fs_url or ""

    # Verify we have valid URLs (not empty strings)
    missing = []
    if not db_url:
        missing.append("MCP_DB_SERVER_URL")
    if not fs_url:
        missing.append("MCP_FS_SERVER_URL")

    if missing:
        pytest.skip(f"CLI tests skipped, missing env vars: {missing}")

    # Verify URLs point to localhost (for local db and fs testing)
    if db_url and "localhost" not in db_url and "127.0.0.1" not in db_url:
        pytest.skip(f"CLI tests skipped, MCP_DB_SERVER_URL is not local: {db_url}")
    if fs_url and "localhost" not in fs_url and "127.0.0.1" not in fs_url:
        pytest.skip(f"CLI tests skipped, MCP_FS_SERVER_URL is not local: {fs_url}")


def test_check_mcp_servers_reachable():
    """Test that check-mcp validates MCP servers are reachable.

    This test verifies that _validate_mcp_servers() successfully
    validates that both DB and FS MCP servers are running and reachable.
    """
    # This should not raise if servers are running
    try:
        _validate_mcp_servers()
    except RuntimeError as e:
        pytest.fail(
            f"MCP servers are not reachable. Make sure both servers are running:\n"
            f"  Terminal 1: python mcp/db_server/main.py\n"
            f"  Terminal 2: python mcp/fs_server/main.py\n"
            f"Error: {e}"
        )


@pytest.mark.asyncio
async def test_cli_client_simple_input(test_user_id: str):
    """Test CLI client command with simple input uses local db and fs.

    This test verifies that the CLI client command works correctly
    with a simple input, using local MCP servers (which use local
    database and filesystem).

    Args:
        test_user_id: Test user ID fixture from conftest.py
    """
    config = ClientConfig(
        input_text="hello",
        athlete_id=1,
        user_id=test_user_id,
        days=60,
        days_to_race=None,
        output_file=None,
        pretty=True,
    )

    # This should complete successfully if MCP servers are running
    # and local db/fs are accessible
    try:
        await _run_client_async(config)
    except Exception as e:
        pytest.fail(
            f"CLI client command failed. Make sure:\n"
            f"  1. MCP DB server is running: python mcp/db_server/main.py\n"
            f"  2. MCP FS server is running: python mcp/fs_server/main.py\n"
            f"  3. Local database is accessible\n"
            f"  4. Local filesystem is accessible\n"
            f"Error: {e}"
        )


@pytest.mark.asyncio
async def test_cli_client_with_output_file(test_user_id: str, tmp_path: Path):
    """Test CLI client command writes output to file correctly.

    This test verifies that the CLI can write output to a file
    using the local filesystem.

    Args:
        test_user_id: Test user ID fixture from conftest.py
        tmp_path: Temporary directory fixture from pytest
    """
    output_file = tmp_path / "cli_output.txt"

    config = ClientConfig(
        input_text="hello",
        athlete_id=1,
        user_id=test_user_id,
        days=60,
        days_to_race=None,
        output_file=str(output_file),
        pretty=True,
    )

    # Run CLI client
    await _run_client_async(config)

    # Verify output file was created
    assert output_file.exists(), "Output file should be created"

    # Verify output file has content
    content = output_file.read_text(encoding="utf-8")
    assert len(content) > 0, "Output file should have content"
    assert "hello" in content.lower() or len(content) > 10, "Output file should contain response"
