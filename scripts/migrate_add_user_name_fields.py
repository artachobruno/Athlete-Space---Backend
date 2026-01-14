"""Migration script to add first_name and last_name fields to users table.

This migration adds:
- first_name: User's first name (nullable)
- last_name: User's last name (nullable)
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


def migrate_add_user_name_fields() -> None:
    """Add first_name and last_name fields to users table."""
    print("Starting migration: add first_name and last_name to users table")

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

        # Add first_name column if it doesn't exist
        if not _column_exists(conn, "users", "first_name"):
            print("Adding first_name column to users table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE users ADD COLUMN first_name VARCHAR"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN first_name VARCHAR"))
            print("✓ Added first_name column")
        else:
            print("first_name column already exists, skipping")

        # Add last_name column if it doesn't exist
        if not _column_exists(conn, "users", "last_name"):
            print("Adding last_name column to users table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE users ADD COLUMN last_name VARCHAR"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_name VARCHAR"))
            print("✓ Added last_name column")
        else:
            print("last_name column already exists, skipping")

    print("Migration complete: first_name and last_name fields added to users table")


if __name__ == "__main__":
    migrate_add_user_name_fields()
