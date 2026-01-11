"""Create a dev-only bootstrap user with email/password auth.

Creates a real user that uses the same auth flow as production:
- Email + password authentication
- Real user schema (no shortcuts)
- Works with frontend login
- Creates associated Athlete or Coach record based on type
"""

import argparse
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
from app.db.models import AuthProvider, User
from app.db.session import get_session
from app.users.athlete_repository import AthleteRepository
from app.users.coach_repository import CoachRepository

# Default dev user credentials (dev-only, never commit to prod)
DEFAULT_EMAIL = "athlete_test@example.com"
DEFAULT_PASSWORD = "password123"  # fake, dev-only


def create_dev_user(email: str, user_type: str, password: str = DEFAULT_PASSWORD) -> str:
    """Create a dev user with email/password auth and associated Athlete or Coach.

    Args:
        email: User email address
        user_type: 'athlete' or 'coach'
        password: User password (default: DEFAULT_PASSWORD)

    Returns:
        Created user_id
    """
    if user_type not in {"athlete", "coach"}:
        raise ValueError(f"user_type must be 'athlete' or 'coach', got: {user_type}")

    with get_session() as db:
        # Check if user already exists
        existing_user = db.execute(select(User).where(User.email == email)).first()
        if existing_user:
            user_id = existing_user[0].id
            print(f"✅ User already exists: user_id={user_id}, email={email}")

            # Ensure the appropriate record exists
            if user_type == "athlete":
                athlete = AthleteRepository.get_or_create(db, user_id)
                print(f"✅ Athlete record: athlete_id={athlete.id}")
            else:
                coach = CoachRepository.get_or_create(db, user_id)
                print(f"✅ Coach record: coach_id={coach.id}")
            return user_id

        # Create new user
        user_id = str(uuid.uuid4())
        new_user = User(
            id=user_id,
            email=email,
            password_hash=hash_password(password),
            auth_provider=AuthProvider.password,
            google_sub=None,
            strava_athlete_id=None,
            is_active=True,
            created_at=datetime.now(UTC),
            last_login_at=None,
        )
        db.add(new_user)
        db.commit()

        # Create associated athlete or coach
        if user_type == "athlete":
            athlete = AthleteRepository.get_or_create(db, user_id)
            print("✅ Created dev user:")
            print(f"  user_id: {user_id}")
            print(f"  athlete_id: {athlete.id}")
            print(f"  email: {email}")
            print(f"  password: {password}")
        else:
            coach = CoachRepository.get_or_create(db, user_id)
            print("✅ Created dev user:")
            print(f"  user_id: {user_id}")
            print(f"  coach_id: {coach.id}")
            print(f"  email: {email}")
            print(f"  password: {password}")

        print("\nYou can now log in via the frontend with these credentials.")
        return user_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a dev user with email/password auth (athlete or coach)"
    )
    parser.add_argument(
        "--email",
        type=str,
        default=DEFAULT_EMAIL,
        help=f"User email address (default: {DEFAULT_EMAIL})",
    )
    parser.add_argument(
        "--type",
        type=str,
        choices=["athlete", "coach"],
        default="athlete",
        help="User type: 'athlete' or 'coach' (default: athlete)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=DEFAULT_PASSWORD,
        help=f"User password (default: {DEFAULT_PASSWORD})",
    )
    args = parser.parse_args()

    try:
        user_id = create_dev_user(email=args.email, user_type=args.type, password=args.password)
        print(f"\n✅ Success! Dev user ready: user_id={user_id}")
    except Exception as e:
        print(f"❌ Error creating dev user: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        sys.exit(1)
