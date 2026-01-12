"""Create a test user for CLI testing.

Creates a User and StravaAccount with athlete_id=1 for testing the CLI.
"""

import sys
import uuid
from datetime import UTC, datetime, timezone
from pathlib import Path

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from sqlalchemy import select

from app.db.models import AuthProvider, StravaAccount, User
from app.db.session import get_session


def create_test_user(athlete_id: int = 1) -> str:
    """Create a test user with the given athlete_id.

    Args:
        athlete_id: Strava athlete ID (default: 1)

    Returns:
        Created user_id
    """
    with get_session() as db:
        # Check if user already exists
        existing_user = db.execute(select(User).where(User.strava_athlete_id == athlete_id)).first()
        if existing_user:
            user_id = existing_user[0].id
            print(f"User already exists with athlete_id={athlete_id}, user_id={user_id}")
            return user_id

        # Check if StravaAccount already exists
        existing_account = db.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id))).first()
        if existing_account:
            user_id = existing_account[0].user_id
            print(f"StravaAccount already exists with athlete_id={athlete_id}, user_id={user_id}")
            # Update user with strava_athlete_id if not set
            user_result = db.execute(select(User).where(User.id == user_id)).first()
            if user_result:
                user = user_result[0]
                if not user.strava_athlete_id:
                    user.strava_athlete_id = athlete_id
                    db.commit()
                    print(f"Updated user with strava_athlete_id={athlete_id}")
            return user_id

        # Create new user
        user_id = str(uuid.uuid4())
        new_user = User(
            id=user_id,
            email=f"test_{user_id}@example.com",
            password_hash=None,
            auth_provider=AuthProvider.password,
            strava_athlete_id=athlete_id,
            created_at=datetime.now(UTC),
            last_login_at=None,
        )
        db.add(new_user)

        # Create StravaAccount with dummy tokens (for testing only)
        # In production, tokens would be encrypted, but for testing we use placeholders
        new_account = StravaAccount(
            user_id=user_id,
            athlete_id=str(athlete_id),
            access_token="test_access_token_encrypted",  # Placeholder - would be encrypted in production
            refresh_token="test_refresh_token_encrypted",  # Placeholder - would be encrypted in production
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

        print("Created test user:")
        print(f"  user_id: {user_id}")
        print(f"  athlete_id: {athlete_id}")
        print("  StravaAccount created with placeholder tokens")
        return user_id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Create a test user for CLI testing")
    parser.add_argument(
        "--athlete-id",
        type=int,
        default=1,
        help="Athlete ID to create (default: 1)",
    )
    args = parser.parse_args()

    try:
        user_id = create_test_user(athlete_id=args.athlete_id)
        print(f"\n✅ Success! Test user created with athlete_id={args.athlete_id}")
        print("You can now use this athlete_id in the CLI:")
        print(f"  python cli/cli.py client -i 'hello' --athlete-id {args.athlete_id}")
    except Exception as e:
        print(f"❌ Error creating test user: {e}", file=sys.stderr)
        sys.exit(1)
