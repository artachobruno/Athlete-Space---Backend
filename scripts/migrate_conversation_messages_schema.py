"""Migration script to update conversation_messages table schema.

This migration:
- Adds user_id column if it doesn't exist
- Adds role column and migrates data from sender if needed
- Adds tokens and ts columns if they don't exist
- Creates necessary indexes
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text

from app.config.settings import settings
from app.db.session import SessionLocal


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    if _is_postgresql():
        result = conn.execute(
            text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                    AND column_name = :column_name
                )
            """),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.scalar() is True
    # SQLite
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = result.fetchall()
    return any(col[1] == column_name for col in columns)


def migrate_conversation_messages_schema() -> None:
    """Update conversation_messages table schema to match model."""
    print("Starting migration: conversation_messages schema update")

    db = SessionLocal()
    try:
        conn = db.connection()

        # Add user_id column if it doesn't exist
        if not _column_exists(conn, "conversation_messages", "user_id"):
            print("Adding user_id column...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN user_id VARCHAR"))
            else:
                conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN user_id TEXT"))
            print("Added user_id column.")
        else:
            print("Column user_id already exists, skipping.")

        # Add role column if it doesn't exist
        if not _column_exists(conn, "conversation_messages", "role"):
            print("Adding role column...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN role VARCHAR"))
            else:
                conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN role TEXT"))
            print("Added role column.")

            # Migrate data from sender to role if sender column exists
            if _column_exists(conn, "conversation_messages", "sender"):
                print("Migrating data from sender to role...")
                if _is_postgresql():
                    conn.execute(
                        text("""
                            UPDATE conversation_messages
                            SET role = sender
                            WHERE role IS NULL
                        """)
                    )
                else:
                    conn.execute(
                        text("""
                            UPDATE conversation_messages
                            SET role = sender
                            WHERE role IS NULL
                        """)
                    )
                print("Migrated data from sender to role.")
        else:
            print("Column role already exists, skipping.")

        # Add tokens column if it doesn't exist
        if not _column_exists(conn, "conversation_messages", "tokens"):
            print("Adding tokens column...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN tokens INTEGER DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN tokens INTEGER DEFAULT 0"))
            print("Added tokens column.")
        else:
            print("Column tokens already exists, skipping.")

        # Add ts column if it doesn't exist
        if not _column_exists(conn, "conversation_messages", "ts"):
            print("Adding ts column...")
            if _is_postgresql():
                conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN ts TIMESTAMPTZ"))
            else:
                conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN ts TIMESTAMP"))
            print("Added ts column.")

            # Backfill ts from created_at if created_at exists
            if _column_exists(conn, "conversation_messages", "created_at"):
                print("Backfilling ts from created_at...")
                if _is_postgresql():
                    conn.execute(
                        text("""
                            UPDATE conversation_messages
                            SET ts = created_at
                            WHERE ts IS NULL
                        """)
                    )
                else:
                    conn.execute(
                        text("""
                            UPDATE conversation_messages
                            SET ts = created_at
                            WHERE ts IS NULL
                        """)
                    )
                print("Backfilled ts from created_at.")
        else:
            print("Column ts already exists, skipping.")

        # Create indexes
        print("Creating indexes...")
        try:
            if _is_postgresql():
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_conversation_messages_user_id ON conversation_messages(user_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_conversation_ts ON conversation_messages(conversation_id, ts)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_user_ts ON conversation_messages(user_id, ts)"))
            else:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_conversation_messages_user_id ON conversation_messages(user_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_conversation_ts ON conversation_messages(conversation_id, ts)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_messages_user_ts ON conversation_messages(user_id, ts)"))
            print("Created indexes.")
        except Exception as e:
            print(f"Some indexes may already exist: {e}")

        db.commit()
        print("Migration complete: Updated conversation_messages table schema.")
    except Exception as e:
        db.rollback()
        print(f"Migration failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_conversation_messages_schema()
