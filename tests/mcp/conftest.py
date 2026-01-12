"""Shared fixtures for MCP tests."""

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

# Add project root to path
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from app.coach.agents.orchestrator_deps import CoachDeps
from app.coach.mcp_client import MCP_CALL_LOG
from app.coach.services.state_builder import build_athlete_state
from app.config.settings import settings
from app.db.models import AuthProvider, StravaAccount, User
from app.db.session import get_session

# Test constants
TEST_ATHLETE_ID = 1


@pytest.fixture(scope="session", autouse=True)
def require_mcp_env():
    """Require MCP environment variables to be set.

    Uses settings defaults if environment variables are not explicitly set.
    Skips all tests if MCP servers are not configured.
    """
    # Use settings defaults if env vars not set
    db_url = os.getenv("MCP_DB_SERVER_URL") or settings.mcp_db_server_url
    fs_url = os.getenv("MCP_FS_SERVER_URL") or settings.mcp_fs_server_url

    # Set environment variables so they're available to the MCP client
    if not os.getenv("MCP_DB_SERVER_URL"):
        os.environ["MCP_DB_SERVER_URL"] = db_url
    if not os.getenv("MCP_FS_SERVER_URL"):
        os.environ["MCP_FS_SERVER_URL"] = fs_url

    # Verify we have valid URLs (not empty strings)
    missing = []
    if not db_url:
        missing.append("MCP_DB_SERVER_URL")
    if not fs_url:
        missing.append("MCP_FS_SERVER_URL")

    if missing:
        pytest.skip(f"MCP tests skipped, missing env vars: {missing}")


@pytest.fixture(scope="session")
def test_user_id():
    """Ensure test user exists and return user_id.

    Creates a test user with athlete_id=1 if it doesn't exist.
    Returns the user_id for use in tests.

    Note: This creates the user in the local test database. If the MCP server
    uses a different database (e.g., Render-hosted), the USER_NOT_FOUND error
    when saving context is expected and handled gracefully (logged as warning).
    """
    with get_session() as db:
        # Check if StravaAccount exists for athlete_id=1
        existing_account = db.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(TEST_ATHLETE_ID))).first()

        if existing_account:
            user_id = existing_account[0].user_id
            # Verify user exists
            user_result = db.execute(select(User).where(User.id == user_id)).first()
            if user_result:
                return user_id

        # Create test user if it doesn't exist
        user_id = str(uuid.uuid4())
        new_user = User(
            id=user_id,
            email=f"test_{user_id}@example.com",
            password_hash=None,
            auth_provider=AuthProvider.password,
            strava_athlete_id=TEST_ATHLETE_ID,
            created_at=datetime.now(UTC),
            last_login_at=None,
        )
        db.add(new_user)

        # Create StravaAccount
        new_account = StravaAccount(
            user_id=user_id,
            athlete_id=str(TEST_ATHLETE_ID),
            access_token="test_access_token_encrypted",
            refresh_token="test_refresh_token_encrypted",
            expires_at=2147483647,  # Max PostgreSQL integer (Jan 19, 2038)
            last_sync_at=None,
            oldest_synced_at=None,
            full_history_synced=False,
            sync_success_count=0,
            sync_failure_count=0,
            last_sync_error=None,
            created_at=datetime.now(UTC),
        )
        db.add(new_account)
        db.commit()

        return user_id


@pytest.fixture
def deps(test_user_id: str):
    """Create CoachDeps for testing with a valid athlete_state."""
    # Create a minimal athlete_state for testing
    # This allows tools to run and call MCP functions
    daily_load = [1.0] * 30  # 30 days of 1 hour training
    athlete_state = build_athlete_state(
        ctl=50.0,
        atl=7.0,
        tsb=43.0,
        daily_load=daily_load,
        days_to_race=None,
    )
    return CoachDeps(
        athlete_id=TEST_ATHLETE_ID,
        user_id=test_user_id,
        athlete_state=athlete_state,
        athlete_profile=None,
        days=60,
        days_to_race=None,
    )


@pytest.fixture(autouse=True)
def clear_mcp_log():
    """Clear MCP call log before and after each test."""
    MCP_CALL_LOG.clear()
    yield
    MCP_CALL_LOG.clear()
