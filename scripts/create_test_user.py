"""Create a test user for CLI testing.

Creates a User with email/password auth and StravaAccount with athlete_id=1 for testing the CLI.
"""

import argparse
import sys
import uuid
from datetime import UTC, datetime, timezone
from pathlib import Path

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from sqlalchemy import select

from app.core.password import hash_password
from app.db.models import AuthProvider, StravaAccount, User
from app.db.session import get_session

# Default test user credentials (dev-only, never commit to prod)
DEFAULT_EMAIL = "test@example.com"
DEFAULT_PASSWORD = "password123"  # noqa: S105 fake, dev-only


def create_test_user(athlete_id: int = 1, email: str | None = None, password: str = DEFAULT_PASSWORD) -> str:
    """Create a test user with the given athlete_id, email, and password.

    Args:
        athlete_id: Strava athlete ID (default: 1)
        email: User email address (default: test@example.com)
        password: User password (default: password123)

    Returns:
        Created user_id
    """
    if email is None:
        email = DEFAULT_EMAIL

    normalized_email = email.lower().strip()

    with get_session() as db:
        # Check if StravaAccount already exists for this athlete_id
        existing_account = db.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id))).first()
        if existing_account:
            user_id = existing_account[0].user_id
            user_result = db.execute(select(User).where(User.id == user_id)).first()
            if user_result:
                print(f"✅ StravaAccount already exists with athlete_id={athlete_id}, user_id={user_id}")
                print(f"   Email: {user_result[0].email}")
                return user_id

        # Check if user with this email already exists
        existing_user = db.execute(select(User).where(User.email == normalized_email)).first()
        if existing_user:
            user_id = existing_user[0].id
            # Check if StravaAccount exists for this user
            account_result = db.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
            if account_result:
                print(f"✅ User already exists: user_id={user_id}, email={normalized_email}, athlete_id={account_result[0].athlete_id}")
                return user_id
            # Create StravaAccount if it doesn't exist
            # Convert Unix timestamp (2147483647 = Jan 19, 2038) to datetime
            expires_at_dt = datetime.fromtimestamp(2147483647, tz=UTC)
            new_account = StravaAccount(
                user_id=user_id,
                athlete_id=str(athlete_id),
                access_token="test_access_token_encrypted",
                refresh_token="test_refresh_token_encrypted",
                expires_at=expires_at_dt,
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
            print(f"✅ Created StravaAccount for existing user: user_id={user_id}, athlete_id={athlete_id}")
            return user_id

        # Create new user with email/password
        user_id = str(uuid.uuid4())
        password_hash_value = hash_password(password)
        new_user = User(
            id=user_id,
            email=normalized_email,
            password_hash=password_hash_value,
            auth_provider="email",  # Database CHECK constraint expects 'email', not 'password'
            google_sub=None,
            created_at=datetime.now(UTC),
            last_login_at=None,
        )
        db.add(new_user)
        db.flush()  # Flush to ensure User is inserted before StravaAccount foreign key check

        # Create StravaAccount with dummy tokens (for testing only)
        # In production, tokens would be encrypted, but for testing we use placeholders
        # Convert Unix timestamp (2147483647 = Jan 19, 2038) to datetime
        expires_at_dt = datetime.fromtimestamp(2147483647, tz=UTC)
        new_account = StravaAccount(
            user_id=user_id,
            athlete_id=str(athlete_id),
            access_token="test_access_token_encrypted",  # Placeholder - would be encrypted in production
            refresh_token="test_refresh_token_encrypted",  # Placeholder - would be encrypted in production
            expires_at=expires_at_dt,  # Max PostgreSQL timestamp (Jan 19, 2038)
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

        print("✅ Created test user:")
        print(f"  user_id: {user_id}")
        print(f"  email: {normalized_email}")
        print(f"  password: {password}")
        print(f"  athlete_id: {athlete_id}")
        print("  StravaAccount created with placeholder tokens")
        print("\nYou can now use this user in the CLI:")
        print(f"  python cli/cli.py client -i 'hello' --athlete-id {athlete_id}")
        print("\nOr log in via the frontend with:")
        print(f"  Email: {normalized_email}")
        print(f"  Password: {password}")
        return user_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a test user for CLI testing with email/password auth")
    parser.add_argument(
        "--athlete-id",
        type=int,
        default=1,
        help="Athlete ID to create (default: 1)",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=DEFAULT_EMAIL,
        help=f"User email address (default: {DEFAULT_EMAIL})",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=DEFAULT_PASSWORD,
        help=f"User password (default: {DEFAULT_PASSWORD})",
    )
    args = parser.parse_args()

    try:
        user_id = create_test_user(athlete_id=args.athlete_id, email=args.email, password=args.password)
        print(f"\n✅ Success! Test user created with athlete_id={args.athlete_id}")
    except Exception as e:
        print(f"❌ Error creating test user: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
