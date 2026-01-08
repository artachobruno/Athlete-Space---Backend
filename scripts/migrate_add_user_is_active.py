"""Migration script to add is_active field to users table.

This migration adds:
- is_active: Boolean flag indicating if user account is active (default: True)

All existing users will be set to is_active=True (active).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text

from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
    else:
        # SQLite
        result = conn.execute(text(f"PRAGMA table_info({table_name})"))
        columns = [row[1] for row in result.fetchall()]
        return column_name in columns
    return result.fetchone() is not None


def migrate_add_user_is_active() -> None:
    """Add is_active field to users table."""
    print("Starting migration: add is_active to users table")

    with engine.begin() as conn:
        # Check if users table exists
        if _is_postgresql():
            result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'users'"))
        else:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'"))
        table_exists = result.fetchone() is not None

        if not table_exists:
            print("users table does not exist. It will be created by Base.metadata.create_all()")
            return

        # Add is_active column if it doesn't exist
        if not _column_exists(conn, "users", "is_active"):
            print("Adding is_active column to users table...")
            if _is_postgresql():
                # PostgreSQL: Add column with default True, then update existing rows
                conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE"))
            else:
                # SQLite: Add column with default True, then update existing rows
                conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))
            print("✓ Added is_active column")

            # Update all existing users to be active (default is already True, but explicit update for clarity)
            if _is_postgresql():
                conn.execute(text("UPDATE users SET is_active = TRUE WHERE is_active IS NULL"))
            else:
                conn.execute(text("UPDATE users SET is_active = 1 WHERE is_active IS NULL"))
            print("✓ Set all existing users to is_active=True")
        else:
            print("is_active column already exists, skipping")

    print("Migration complete: is_active field added to users table")


if __name__ == "__main__":
    migrate_add_user_is_active()
