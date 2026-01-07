"""Migration script to update coach_messages table schema.

This migration:
- Adds new columns: user_id, created_at
- Migrates data from old columns (athlete_id -> user_id, timestamp -> created_at)
- Keeps old columns temporarily for backward compatibility

The old columns (athlete_id, timestamp) should be dropped in a future migration
after all code is updated to use the new schema.
"""

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


def _table_exists(conn) -> bool:
    """Check if coach_messages table exists."""
    if _is_postgresql():
        result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'coach_messages'"))
        return result.fetchone() is not None
    result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='coach_messages'"))
    return result.fetchone() is not None


def _column_exists(conn, column_name: str) -> bool:
    """Check if column exists in coach_messages table."""
    if _is_postgresql():
        result = conn.execute(
            text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'coach_messages'
                    AND column_name = :column_name
                )
            """),
            {"column_name": column_name},
        )
        return result.scalar() is True
    # SQLite
    result = conn.execute(text("PRAGMA table_info(coach_messages)"))
    columns = result.fetchall()
    return any(col[1] == column_name for col in columns)


def migrate_coach_messages_schema() -> None:
    """Update coach_messages table schema to match model."""
    with engine.begin() as conn:
        if not _table_exists(conn):
            print("coach_messages table does not exist. Skipping migration (table will be created by Base.metadata.create_all).")
            return

        # Check existing columns
        if _is_postgresql():
            result = conn.execute(
                text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'coach_messages'
                """)
            )
            existing_columns = {row[0] for row in result.fetchall()}
        else:
            result = conn.execute(text("PRAGMA table_info(coach_messages)"))
            existing_columns = {row[1] for row in result.fetchall()}

        print(f"Existing columns: {sorted(existing_columns)}")

        # Add user_id column if it doesn't exist
        if "user_id" not in existing_columns:
            print("Adding user_id column...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE coach_messages ADD COLUMN user_id VARCHAR"))
            else:
                conn.execute(text("ALTER TABLE coach_messages ADD COLUMN user_id TEXT"))
            print("Added user_id column.")

            # Migrate data: map athlete_id to user_id via strava_accounts
            print("Migrating athlete_id to user_id...")
            if _is_postgresql():
                conn.execute(
                    text("""
                        UPDATE coach_messages cm
                        SET user_id = sa.user_id
                        FROM strava_accounts sa
                        WHERE cm.athlete_id = CAST(sa.athlete_id AS INTEGER)
                        AND cm.user_id IS NULL
                    """)
                )
            else:
                # SQLite
                conn.execute(
                    text("""
                        UPDATE coach_messages
                        SET user_id = (
                            SELECT user_id
                            FROM strava_accounts
                            WHERE CAST(athlete_id AS TEXT) = CAST(coach_messages.athlete_id AS TEXT)
                            LIMIT 1
                        )
                        WHERE user_id IS NULL
                    """)
                )
            print("Migrated athlete_id to user_id.")
        else:
            print("Column user_id already exists, skipping.")

        # Add created_at column if it doesn't exist
        if "created_at" not in existing_columns:
            print("Adding created_at column...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE coach_messages ADD COLUMN created_at TIMESTAMP"))
            else:
                conn.execute(text("ALTER TABLE coach_messages ADD COLUMN created_at DATETIME"))
            print("Added created_at column.")

            # Migrate data: copy timestamp to created_at
            if "timestamp" in existing_columns:
                print("Migrating timestamp to created_at...")
                if _is_postgresql():
                    conn.execute(
                        text("""
                            UPDATE coach_messages
                            SET created_at = timestamp
                            WHERE created_at IS NULL AND timestamp IS NOT NULL
                        """)
                    )
                else:
                    conn.execute(
                        text("""
                            UPDATE coach_messages
                            SET created_at = timestamp
                            WHERE created_at IS NULL AND timestamp IS NOT NULL
                        """)
                    )
                print("Migrated timestamp to created_at.")
            else:
                # No timestamp column, set default to current time for existing rows
                print("No timestamp column found, setting created_at to current time for existing rows...")
                if _is_postgresql():
                    conn.execute(
                        text("""
                            UPDATE coach_messages
                            SET created_at = CURRENT_TIMESTAMP
                            WHERE created_at IS NULL
                        """)
                    )
                else:
                    conn.execute(
                        text("""
                            UPDATE coach_messages
                            SET created_at = datetime('now')
                            WHERE created_at IS NULL
                        """)
                    )
                print("Set created_at to current time for existing rows.")
        else:
            print("Column created_at already exists, skipping.")

        # Add index on user_id (always try, IF NOT EXISTS handles duplicates)
        print("Creating index on user_id...")
        try:
            if _is_postgresql():
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_coach_messages_user_id ON coach_messages (user_id)"))
            else:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_coach_messages_user_id ON coach_messages (user_id)"))
            print("Created index on user_id.")
        except Exception as e:
            print(f"Index may already exist: {e}")

        print("Migration complete: Updated coach_messages table schema.")
        print("Note: Old columns (athlete_id, timestamp) are kept for backward compatibility.")
        print("Update code to use user_id and created_at, then drop old columns in a future migration.")


if __name__ == "__main__":
    migrate_coach_messages_schema()
