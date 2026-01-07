"""Migration script to add target_races column to athlete_profiles table.

This migration adds:
- target_races: JSON column (nullable) for storing list of target race names

Supports both SQLite and PostgreSQL databases.
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


def migrate_add_target_races() -> None:
    """Add target_races column to athlete_profiles table."""
    print("Starting migration: add target_races column to athlete_profiles")

    with engine.begin() as conn:
        # Check if athlete_profiles table exists
        if _is_postgresql():
            result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'athlete_profiles'"))
        else:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='athlete_profiles'"))
        table_exists = result.fetchone() is not None

        if not table_exists:
            print("athlete_profiles table does not exist. It will be created by Base.metadata.create_all()")
            return

        # Add target_races column if it doesn't exist
        if not _column_exists(conn, "athlete_profiles", "target_races"):
            print("Adding target_races column to athlete_profiles table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN target_races JSONB"))
            else:
                # SQLite uses JSON type (stored as TEXT)
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN target_races JSON"))
            print("✓ Added target_races column")
        else:
            print("target_races column already exists, skipping")

    print("Migration complete: target_races column added to athlete_profiles")


if __name__ == "__main__":
    migrate_add_target_races()
