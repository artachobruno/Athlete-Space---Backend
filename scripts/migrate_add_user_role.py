"""Migration script to add role field to users table.

This migration adds:
- role: String field indicating user role (default: 'athlete')
  Allowed values: 'athlete', 'coach'

All existing users will be set to role='athlete' (default).
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


def _enum_type_exists(conn, enum_name: str) -> bool:
    """Check if an enum type exists in PostgreSQL."""
    if not _is_postgresql():
        return False
    result = conn.execute(
        text(
            """
            SELECT typname
            FROM pg_type
            WHERE typname = :enum_name
            """
        ),
        {"enum_name": enum_name},
    )
    return result.fetchone() is not None


def migrate_add_user_role() -> None:
    """Add role field to users table."""
    print("Starting migration: add role to users table")

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

        # Add role column if it doesn't exist
        if not _column_exists(conn, "users", "role"):
            print("Adding role column to users table...")
            if _is_postgresql():
                # Create enum type if it doesn't exist
                if not _enum_type_exists(conn, "userrole"):
                    print("Creating userrole enum type...")
                    conn.execute(text("CREATE TYPE userrole AS ENUM ('athlete', 'coach')"))
                    print("✓ Created userrole enum type")
                else:
                    print("userrole enum type already exists, skipping")
                # PostgreSQL: Add column with enum type and default 'athlete'
                conn.execute(text("ALTER TABLE users ADD COLUMN role userrole NOT NULL DEFAULT 'athlete'"))
            else:
                # SQLite: Add column (SQLite 3.37.0+ supports NOT NULL DEFAULT, older versions may need workaround)
                try:
                    conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR NOT NULL DEFAULT 'athlete'"))
                except Exception as e:
                    # Fallback for older SQLite: add nullable, update, then we rely on application default
                    print(f"Warning: Direct NOT NULL DEFAULT failed, using fallback: {e}")
                    conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR"))
                    conn.execute(text("UPDATE users SET role = 'athlete' WHERE role IS NULL"))
            print("✓ Added role column")

            # Update all existing users to be athletes (default is already 'athlete', but explicit update for clarity)
            conn.execute(text("UPDATE users SET role = 'athlete' WHERE role IS NULL OR role = ''"))
            print("✓ Set all existing users to role='athlete'")
        else:
            print("role column already exists, skipping")

    print("Migration complete: role field added to users table")


if __name__ == "__main__":
    migrate_add_user_role()
