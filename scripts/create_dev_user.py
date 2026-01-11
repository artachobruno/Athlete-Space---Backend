"""Create a dev-only bootstrap user with email/password auth.

Creates a real user that uses the same auth flow as production:
- Email + password authentication
- Real user schema (no shortcuts)
- Works with frontend login
- Creates associated Athlete record
"""

import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from sqlalchemy import select

from app.core.password import hash_password
from app.db.models import Athlete, AuthProvider, User
from app.db.session import get_session
from app.users.athlete_repository import AthleteRepository

# Dev user credentials (dev-only, never commit to prod)
EMAIL = "athlete_test@example.com"
PASSWORD = "password123"  # fake, dev-only


def create_dev_user() -> str:
    """Create a dev user with email/password auth and associated Athlete.

    Returns:
        Created user_id
    """
    with get_session() as db:
        # Check if user already exists
        existing_user = db.execute(select(User).where(User.email == EMAIL)).first()
        if existing_user:
            user_id = existing_user[0].id
            print(f"✅ User already exists: user_id={user_id}, email={EMAIL}")
            # Ensure athlete exists
            athlete = AthleteRepository.get_or_create(db, user_id)
            print(f"✅ Athlete record: athlete_id={athlete.id}")
            return user_id

        # Create new user
        user_id = str(uuid.uuid4())
        new_user = User(
            id=user_id,
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
            auth_provider=AuthProvider.password,
            google_sub=None,
            strava_athlete_id=None,
            is_active=True,
            created_at=datetime.now(UTC),
            last_login_at=None,
        )
        db.add(new_user)
        db.commit()

        # Create associated athlete (lazy creation pattern)
        athlete = AthleteRepository.get_or_create(db, user_id)

        print("✅ Created dev user:")
        print(f"  user_id: {user_id}")
        print(f"  athlete_id: {athlete.id}")
        print(f"  email: {EMAIL}")
        print(f"  password: {PASSWORD}")
        print("\nYou can now log in via the frontend with these credentials.")
        return user_id


if __name__ == "__main__":
    try:
        user_id = create_dev_user()
        print(f"\n✅ Success! Dev user ready: user_id={user_id}")
    except Exception as e:
        print(f"❌ Error creating dev user: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
