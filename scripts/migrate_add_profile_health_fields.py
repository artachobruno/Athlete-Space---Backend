"""Migration script to add health and constraint fields to athlete_profiles table.

This migration adds:
- injury_history: JSON column (nullable) for storing list of historical injuries
- current_injuries: JSON column (nullable) for storing list of current injuries
- training_constraints: TEXT column (nullable) for storing training constraints

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


def migrate_add_profile_health_fields() -> None:
    """Add health and constraint fields to athlete_profiles table."""
    print("Starting migration: add health and constraint fields to athlete_profiles")

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

        # Add injury_history column if it doesn't exist
        if not _column_exists(conn, "athlete_profiles", "injury_history"):
            print("Adding injury_history column to athlete_profiles table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN injury_history JSONB"))
            else:
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN injury_history JSON"))
            print("✓ Added injury_history column")
        else:
            print("injury_history column already exists, skipping")

        # Add current_injuries column if it doesn't exist
        if not _column_exists(conn, "athlete_profiles", "current_injuries"):
            print("Adding current_injuries column to athlete_profiles table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN current_injuries JSONB"))
            else:
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN current_injuries JSON"))
            print("✓ Added current_injuries column")
        else:
            print("current_injuries column already exists, skipping")

        # Add training_constraints column if it doesn't exist
        if not _column_exists(conn, "athlete_profiles", "training_constraints"):
            print("Adding training_constraints column to athlete_profiles table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN training_constraints TEXT"))
            else:
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN training_constraints TEXT"))
            print("✓ Added training_constraints column")
        else:
            print("training_constraints column already exists, skipping")

    print("Migration complete: health and constraint fields added to athlete_profiles")


if __name__ == "__main__":
    migrate_add_profile_health_fields()
