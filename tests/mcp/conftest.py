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
from app.db.models import StravaAccount, User
from app.db.session import get_session

# Test constants
TEST_ATHLETE_ID = 1


@pytest.fixture(scope="session", autouse=True)
def require_mcp_env():
    """Require MCP environment variables to be set.

    Skips all tests if MCP servers are not configured.
    """
    missing = []
    if not os.getenv("MCP_DB_SERVER_URL"):
        missing.append("MCP_DB_SERVER_URL")
    if not os.getenv("MCP_FS_SERVER_URL"):
        missing.append("MCP_FS_SERVER_URL")

    if missing:
        pytest.skip(f"MCP tests skipped, missing env vars: {missing}")


@pytest.fixture(scope="session")
def test_user_id():
    """Ensure test user exists and return user_id.

    Creates a test user with athlete_id=1 if it doesn't exist.
    Returns the user_id for use in tests.
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
            email=None,
            password_hash=None,
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
            expires_at=9999999999,
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
    """Create CoachDeps for testing."""
    return CoachDeps(
        athlete_id=TEST_ATHLETE_ID,
        user_id=test_user_id,
        athlete_state=None,  # Will be populated by tools via MCP if needed
        days=60,
        days_to_race=None,
    )


@pytest.fixture(autouse=True)
def clear_mcp_log():
    """Clear MCP call log before and after each test."""
    MCP_CALL_LOG.clear()
    yield
    MCP_CALL_LOG.clear()
