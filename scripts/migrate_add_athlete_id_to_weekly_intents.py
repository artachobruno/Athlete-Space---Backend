"""Migration script to add missing columns to weekly_intents table.

This migration adds columns to match the SQLAlchemy model definition:
- athlete_id: Required column, populated from user_id via StravaAccount lookup
- primary_focus: Optional metadata column (can be backfilled from intent_data)
- total_sessions: Optional metadata column (can be backfilled from intent_data)
- target_volume_hours: Optional metadata column (can be backfilled from intent_data)
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text

from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if using PostgreSQL database."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = :table_name
                AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result.fetchall()]
    return column_name in columns


def migrate_add_athlete_id_to_weekly_intents() -> None:
    """Add missing columns to weekly_intents table to match SQLAlchemy model."""
    print("Starting migration: add missing columns to weekly_intents")

    with engine.begin() as conn:
        if not _is_postgresql():
            print("⚠️  SQLite detected - migration may not work correctly")
            print("   This migration is designed for PostgreSQL")
            return

        # Track what needs to be added
        columns_to_add = []

        if not _column_exists(conn, "weekly_intents", "athlete_id"):
            columns_to_add.append(("athlete_id", "INTEGER"))
        if not _column_exists(conn, "weekly_intents", "primary_focus"):
            columns_to_add.append(("primary_focus", "VARCHAR"))
        if not _column_exists(conn, "weekly_intents", "total_sessions"):
            columns_to_add.append(("total_sessions", "INTEGER"))
        if not _column_exists(conn, "weekly_intents", "target_volume_hours"):
            columns_to_add.append(("target_volume_hours", "FLOAT"))

        if not columns_to_add:
            print("✅ All columns already exist in weekly_intents table")
            return

        print(f"Adding {len(columns_to_add)} column(s) to weekly_intents table...")

        # Track if athlete_id was added (needs special handling)
        athlete_id_was_added = False

        # Add all missing columns
        for column_name, column_type in columns_to_add:
            print(f"  Adding column: {column_name}")
            conn.execute(
                text(
                    f"""
                    ALTER TABLE weekly_intents
                    ADD COLUMN {column_name} {column_type}
                    """
                )
            )
            if column_name == "athlete_id":
                athlete_id_was_added = True

        # Special handling for athlete_id: populate and make NOT NULL
        if athlete_id_was_added:
            print("Populating athlete_id from user_id via StravaAccount...")

            # Backfill athlete_id from user_id via StravaAccount
            conn.execute(
                text(
                    """
                    UPDATE weekly_intents wi
                    SET athlete_id = (
                        SELECT CAST(sa.athlete_id AS INTEGER)
                        FROM strava_accounts sa
                        WHERE sa.user_id = wi.user_id
                        LIMIT 1
                    )
                    WHERE wi.athlete_id IS NULL
                    """
                )
            )

            print("Making athlete_id NOT NULL and adding index...")

            # Make it NOT NULL
            conn.execute(
                text(
                    """
                    ALTER TABLE weekly_intents
                    ALTER COLUMN athlete_id SET NOT NULL
                    """
                )
            )

            # Add index if it doesn't exist
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_weekly_intents_athlete_id
                    ON weekly_intents(athlete_id)
                    """
                )
            )

        print(f"✅ Successfully added {len(columns_to_add)} column(s) to weekly_intents")


if __name__ == "__main__":
    migrate_add_athlete_id_to_weekly_intents()
