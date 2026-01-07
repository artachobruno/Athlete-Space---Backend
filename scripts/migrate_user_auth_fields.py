"""Migration script to add authentication fields to users table.

This migration adds:
- password_hash: Hashed password (nullable string)
- strava_athlete_id: Strava athlete ID (nullable integer, unique, indexed)
- last_login_at: Last login timestamp (nullable datetime)

Also adds unique indexes on email and strava_athlete_id if they don't exist.
"""

from __future__ import annotations

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


def migrate_user_auth_fields() -> None:
    """Add authentication fields to users table."""
    print("Starting migration: user authentication fields")

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

        # Add password_hash column if it doesn't exist
        if not _column_exists(conn, "users", "password_hash"):
            print("Adding password_hash column to users table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR"))
            print("✓ Added password_hash column")
        else:
            print("password_hash column already exists, skipping")

        # Add strava_athlete_id column if it doesn't exist
        if not _column_exists(conn, "users", "strava_athlete_id"):
            print("Adding strava_athlete_id column to users table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE users ADD COLUMN strava_athlete_id INTEGER UNIQUE"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN strava_athlete_id INTEGER"))
            print("✓ Added strava_athlete_id column")
        else:
            print("strava_athlete_id column already exists, skipping")

        # Add last_login_at column if it doesn't exist
        if not _column_exists(conn, "users", "last_login_at"):
            print("Adding last_login_at column to users table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP"))
            else:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP"))
            print("✓ Added last_login_at column")
        else:
            print("last_login_at column already exists, skipping")

        # Add unique constraint on email if it doesn't exist (SQLite doesn't support ALTER TABLE ADD CONSTRAINT)
        if _is_postgresql():
            # Check if unique constraint exists on email
            result = conn.execute(
                text(
                    """
                    SELECT constraint_name
                    FROM information_schema.table_constraints
                    WHERE table_name = 'users'
                    AND constraint_type = 'UNIQUE'
                    AND constraint_name LIKE '%email%'
                    """
                )
            )
            if not result.fetchone():
                # Try to add unique constraint (may fail if duplicate emails exist)
                try:
                    conn.execute(text("ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email)"))
                    print("✓ Added unique constraint on email")
                except Exception as e:
                    print(f"⚠ Could not add unique constraint on email (may have duplicates): {e}")
        # For SQLite, unique constraint is handled at the table definition level

        # Add unique index on strava_athlete_id if it doesn't exist (only if column exists)
        if _column_exists(conn, "users", "strava_athlete_id"):
            if not _index_exists(conn, "users", "idx_users_strava_athlete_id"):
                print("Creating unique index on strava_athlete_id...")
                if _is_postgresql():
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_strava_athlete_id ON users(strava_athlete_id)"))
                else:
                    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_strava_athlete_id ON users(strava_athlete_id)"))
                print("✓ Created unique index on strava_athlete_id")
            else:
                print("idx_users_strava_athlete_id index already exists, skipping")

        # Add index on email if it doesn't exist
        if not _index_exists(conn, "users", "idx_users_email"):
            print("Creating index on email...")
            if _is_postgresql():
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))
            else:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))
            print("✓ Created index on email")
        else:
            print("idx_users_email index already exists, skipping")

    print("Migration complete: user authentication fields added")


if __name__ == "__main__":
    migrate_user_auth_fields()
