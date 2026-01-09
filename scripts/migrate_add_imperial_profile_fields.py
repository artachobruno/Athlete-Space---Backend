"""Migration script to add imperial weight and height fields to athlete_profiles table.

This migration adds:
- weight_lbs: FLOAT column (nullable) for storing weight in pounds
- height_in: FLOAT column (nullable) for storing height in inches

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


def migrate_add_imperial_profile_fields() -> None:
    """Add imperial weight and height fields to athlete_profiles table."""
    print("Starting migration: add imperial weight and height fields to athlete_profiles")

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

        # Add weight_lbs column if it doesn't exist
        if not _column_exists(conn, "athlete_profiles", "weight_lbs"):
            print("Adding weight_lbs column to athlete_profiles table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN weight_lbs DOUBLE PRECISION"))
            else:
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN weight_lbs REAL"))
            print("✓ Added weight_lbs column")
        else:
            print("weight_lbs column already exists, skipping")

        # Add height_in column if it doesn't exist
        if not _column_exists(conn, "athlete_profiles", "height_in"):
            print("Adding height_in column to athlete_profiles table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN height_in DOUBLE PRECISION"))
            else:
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN height_in REAL"))
            print("✓ Added height_in column")
        else:
            print("height_in column already exists, skipping")

    print("Migration complete: imperial weight and height fields added to athlete_profiles")


if __name__ == "__main__":
    migrate_add_imperial_profile_fields()
