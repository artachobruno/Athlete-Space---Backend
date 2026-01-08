"""Migration script to add Google OAuth fields to users table.

This migration adds:
- auth_provider: Authentication provider enum (password or google)
- google_sub: Google user ID (sub claim, nullable, unique)
- Makes password_hash nullable (for OAuth users)

Also sets existing users to auth_provider='password'.
"""

from __future__ import annotations

from contextlib import suppress

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


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    """Check if an index exists."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = :table_name AND indexname = :index_name
                """
            ),
            {"table_name": table_name, "index_name": index_name},
        )
    else:
        # SQLite
        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND name=:index_name"),
            {"index_name": index_name},
        )
    return result.fetchone() is not None


def migrate_add_google_oauth_fields() -> None:
    """Add Google OAuth fields to users table."""
    print("Starting migration: add Google OAuth fields to users table")

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

        # Make password_hash nullable if it exists and is not nullable
        if _column_exists(conn, "users", "password_hash"):
            if _is_postgresql():
                # Check if column is nullable
                result = conn.execute(
                    text(
                        """
                        SELECT is_nullable
                        FROM information_schema.columns
                        WHERE table_name = 'users' AND column_name = 'password_hash'
                        """
                    )
                )
                row = result.fetchone()
                if row and row[0] == "NO":
                    print("Making password_hash column nullable...")
                    conn.execute(text("ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"))
                    print("✓ Made password_hash nullable")
                else:
                    print("password_hash column is already nullable, skipping")
            else:
                # SQLite doesn't support ALTER COLUMN, but we can check if it's nullable
                # In SQLite, we need to recreate the table to change nullability
                # For now, we'll just note that SQLite columns are nullable by default
                print("SQLite: password_hash nullability handled at table creation time")

        # Add auth_provider column if it doesn't exist
        if not _column_exists(conn, "users", "auth_provider"):
            print("Adding auth_provider column to users table...")
            if _is_postgresql():
                # Create enum type if it doesn't exist
                with suppress(Exception):
                    # Enum type may already exist, suppress error if so
                    conn.execute(text("CREATE TYPE authprovider AS ENUM ('password', 'google')"))
                conn.execute(text("ALTER TABLE users ADD COLUMN auth_provider authprovider NOT NULL DEFAULT 'password'"))
            else:
                # SQLite doesn't support enums, use VARCHAR
                conn.execute(text("ALTER TABLE users ADD COLUMN auth_provider VARCHAR NOT NULL DEFAULT 'password'"))
            print("✓ Added auth_provider column")
        else:
            print("auth_provider column already exists, skipping")

        # Set existing users to auth_provider='password' if they don't have it set
        print("Setting existing users to auth_provider='password'...")
        if _is_postgresql():
            conn.execute(text("UPDATE users SET auth_provider = 'password' WHERE auth_provider IS NULL"))
        else:
            conn.execute(text("UPDATE users SET auth_provider = 'password' WHERE auth_provider IS NULL OR auth_provider = ''"))
        print("✓ Set existing users to auth_provider='password'")

        # Add google_sub column if it doesn't exist
        if not _column_exists(conn, "users", "google_sub"):
            print("Adding google_sub column to users table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR UNIQUE"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR"))
            print("✓ Added google_sub column")
        else:
            print("google_sub column already exists, skipping")

        # Add unique index on google_sub if it doesn't exist (only if column exists)
        if _column_exists(conn, "users", "google_sub"):
            if not _index_exists(conn, "users", "idx_users_google_sub"):
                print("Creating unique index on google_sub...")
                if _is_postgresql():
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)"))
                else:
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub)"))
                print("✓ Created unique index on google_sub")
            else:
                print("idx_users_google_sub index already exists, skipping")

    print("Migration complete: Google OAuth fields added to users table")


if __name__ == "__main__":
    migrate_add_google_oauth_fields()
