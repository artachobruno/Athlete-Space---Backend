"""Migration script to add historical backfill cursor fields to user_integrations table.

This migration adds:
- historical_backfill_cursor_date: Timestamp tracking how far back we've synced (nullable TIMESTAMPTZ)
- historical_backfill_complete: Whether full history backfill is complete (boolean, default False)

Migration defaults:
- Set historical_backfill_complete = False for all Garmin integrations
- historical_backfill_cursor_date remains NULL (will be set on first backfill)
"""

import sys
from pathlib import Path

# Add project root to Python path (must be absolute for Render/production)
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

# Verify project root contains app directory or pyproject.toml
if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    # If parent doesn't have app/ or pyproject.toml, try current working directory
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        # Last resort: try going up one more level (for cases where script is in src/scripts/)
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

# Ensure project root is in path
project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from sqlalchemy import text

from app.db.session import engine


def _table_exists(conn) -> bool:
    """Check if user_integrations table exists."""
    if "sqlite" in str(engine.url).lower():
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='user_integrations'"))
        return result.fetchone() is not None
    # PostgreSQL
    result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'user_integrations'"))
    return result.fetchone() is not None


def migrate_garmin_history_cursor() -> None:
    """Add historical backfill cursor fields to user_integrations table."""
    with engine.begin() as conn:
        # Check if table exists first
        if not _table_exists(conn):
            print("user_integrations table does not exist. Skipping migration (table will be created by migrate_user_integrations).")
            return

        # Check if columns already exist
        if "sqlite" in str(engine.url).lower():
            result = conn.execute(text("PRAGMA table_info(user_integrations)"))
            columns = [row[1] for row in result.fetchall()]
        else:
            # PostgreSQL
            result = conn.execute(
                text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'user_integrations'
                """)
            )
            columns = [row[0] for row in result.fetchall()]

        # Add historical_backfill_cursor_date column if it doesn't exist
        if "historical_backfill_cursor_date" not in columns:
            print("Adding historical_backfill_cursor_date column...")
            if "sqlite" in str(engine.url).lower():
                conn.execute(text("ALTER TABLE user_integrations ADD COLUMN historical_backfill_cursor_date TIMESTAMP"))
            else:
                conn.execute(text("ALTER TABLE user_integrations ADD COLUMN historical_backfill_cursor_date TIMESTAMPTZ"))
            print("Added historical_backfill_cursor_date column.")

        # Add historical_backfill_complete column if it doesn't exist
        if "historical_backfill_complete" not in columns:
            print("Adding historical_backfill_complete column...")
            if "sqlite" in str(engine.url).lower():
                conn.execute(text("ALTER TABLE user_integrations ADD COLUMN historical_backfill_complete BOOLEAN DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE user_integrations ADD COLUMN historical_backfill_complete BOOLEAN DEFAULT FALSE"))
            print("Added historical_backfill_complete column.")

        # Set migration defaults
        print("Setting migration defaults...")
        if "sqlite" in str(engine.url).lower():
            # SQLite: Set historical_backfill_complete = 0 (False) where NULL
            conn.execute(
                text("""
                    UPDATE user_integrations
                    SET historical_backfill_complete = 0
                    WHERE historical_backfill_complete IS NULL
                """)
            )
        else:
            # PostgreSQL
            conn.execute(
                text("""
                    UPDATE user_integrations
                    SET historical_backfill_complete = FALSE
                    WHERE historical_backfill_complete IS NULL
                """)
            )

        # Create index on historical_backfill_cursor_date for efficient queries
        if "sqlite" not in str(engine.url).lower():
            # PostgreSQL: Check if index exists
            result = conn.execute(
                text("""
                    SELECT indexname
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                    AND tablename = 'user_integrations'
                    AND indexname = 'idx_user_integration_history_cursor'
                """)
            )
            index_exists = result.fetchone() is not None

            if not index_exists:
                print("Creating index on historical_backfill_cursor_date...")
                conn.execute(
                    text("""
                        CREATE INDEX idx_user_integration_history_cursor
                        ON user_integrations(historical_backfill_cursor_date)
                        WHERE provider = 'garmin' AND historical_backfill_cursor_date IS NOT NULL
                    """)
                )
                print("Created index on historical_backfill_cursor_date.")

        print("Migration complete: Added historical backfill cursor fields to user_integrations table.")


if __name__ == "__main__":
    migrate_garmin_history_cursor()
