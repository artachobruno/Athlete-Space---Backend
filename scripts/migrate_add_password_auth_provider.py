#!/usr/bin/env python3
"""Migration script to add 'password' to auth_provider CHECK constraint.

This fixes the constraint mismatch where the code uses 'password' but the DB only allows
'google', 'email', 'apple'.

Run with:
    python scripts/migrate_add_password_auth_provider.py
"""

import os
import sys

from sqlalchemy import create_engine, text

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def migrate() -> None:
    """Add 'password' to auth_provider CHECK constraint."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set")
        sys.exit(1)

    engine = create_engine(database_url)

    with engine.begin() as conn:
        print("Dropping old auth_provider CHECK constraint...")
        try:
            conn.execute(text("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_auth_provider_check"))
            print("✓ Dropped old constraint")
        except Exception as e:
            print(f"Warning: Could not drop constraint (may not exist): {e}")

        print("Adding new auth_provider CHECK constraint with 'password'...")
        conn.execute(
            text(
                "ALTER TABLE users ADD CONSTRAINT users_auth_provider_check "
                "CHECK (auth_provider IN ('google', 'email', 'apple', 'password'))"
            )
        )
        print("✓ Added new constraint")

    print("\n✅ Migration complete! 'password' is now a valid auth_provider value.")


if __name__ == "__main__":
    migrate()
