"""Create a LOCAL test user (no Strava).

- No StravaAccount
- No strava_athlete_id
- Safe for calendar / planner testing
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

from app.db.models import AuthProvider, User
from app.db.session import get_session


def create_local_test_user(email: str | None = None, user_number: int | None = None) -> str:
    with get_session() as db:
        if email:
            existing = db.execute(
                select(User).where(User.email == email)
            ).scalar_one_or_none()
            if existing:
                print(f"User already exists: {email}")
                return existing.id

        user_id = str(uuid.uuid4())
        email = email or f"test_{user_number}@athlete.com"

        user = User(
            id=user_id,
            email=email,
            password_hash=None,
            auth_provider=AuthProvider.password,
            strava_athlete_id=None,   # 🚫 NO STRAVA
            created_at=datetime.now(UTC),
            last_login_at=None,
            is_active=True,
            timezone="UTC",
        )

        db.add(user)
        db.commit()

        print("✅ Local test user created")
        print(f"  id: {user_id}")
        print(f"  email: {email}")
        print("  Strava: NOT LINKED")

        return user_id


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--email",
        type=str,
        help="Optional email (defaults to generated local_test_*)",
    )
    parser.add_argument(
        "--user_number",
        type=int,
        default=None,
    )
    args = parser.parse_args()

    try:
        create_local_test_user(email=args.email, user_number=args.user_number)
    except Exception as e:
        print(f"❌ Failed to create test user: {e}", file=sys.stderr)
        sys.exit(1)
