"""Migration script to add extracted_injury_attributes column to athlete_profiles table.

This migration adds a JSON column to store extracted injury attributes from injury notes extraction.
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


def migrate_add_extracted_injury_attributes() -> None:
    """Add extracted_injury_attributes column to athlete_profiles table."""
    print("Starting migration: add extracted_injury_attributes column to athlete_profiles")

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

        # Add extracted_injury_attributes column if it doesn't exist
        if not _column_exists(conn, "athlete_profiles", "extracted_injury_attributes"):
            print("Adding extracted_injury_attributes column to athlete_profiles table...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN extracted_injury_attributes JSONB"))
            else:
                conn.execute(text("ALTER TABLE athlete_profiles ADD COLUMN extracted_injury_attributes JSON"))
            print("✓ Added extracted_injury_attributes column")
        else:
            print("extracted_injury_attributes column already exists, skipping")

    print("Migration complete: extracted_injury_attributes column added to athlete_profiles")


if __name__ == "__main__":
    migrate_add_extracted_injury_attributes()
