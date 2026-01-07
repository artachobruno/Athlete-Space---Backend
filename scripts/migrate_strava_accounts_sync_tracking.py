"""Migration script to add sync tracking columns to strava_accounts table.

This migration adds:
- sync_success_count: Number of successful syncs (integer, default 0)
- sync_failure_count: Number of failed syncs (integer, default 0)
- last_sync_error: Last sync error message (nullable text)

These columns enable reliability tracking and error monitoring for sync operations.
"""

from sqlalchemy import text

from app.db.session import engine


def _table_exists(conn) -> bool:
    """Check if strava_accounts table exists."""
    if "sqlite" in str(engine.url).lower():
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='strava_accounts'"))
        return result.fetchone() is not None
    # PostgreSQL
    result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'strava_accounts'"))
    return result.fetchone() is not None


def migrate_strava_accounts_sync_tracking() -> None:
    """Add sync tracking columns to strava_accounts table."""
    with engine.begin() as conn:
        # Check if table exists first
        if not _table_exists(conn):
            print("strava_accounts table does not exist. Skipping migration (table will be created by migrate_strava_accounts).")
            return

        # Check if columns already exist
        if "sqlite" in str(engine.url).lower():
            result = conn.execute(text("PRAGMA table_info(strava_accounts)"))
            columns = [row[1] for row in result.fetchall()]
        else:
            # PostgreSQL
            result = conn.execute(
                text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = 'strava_accounts'
                """)
            )
            columns = [row[0] for row in result.fetchall()]

        # Add sync_success_count column if it doesn't exist
        if "sync_success_count" not in columns:
            print("Adding sync_success_count column...")
            if "sqlite" in str(engine.url).lower():
                conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN sync_success_count INTEGER DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN sync_success_count INTEGER NOT NULL DEFAULT 0"))
            print("Added sync_success_count column.")
        else:
            print("Column sync_success_count already exists, skipping.")

        # Add sync_failure_count column if it doesn't exist
        if "sync_failure_count" not in columns:
            print("Adding sync_failure_count column...")
            if "sqlite" in str(engine.url).lower():
                conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN sync_failure_count INTEGER DEFAULT 0"))
            else:
                conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN sync_failure_count INTEGER NOT NULL DEFAULT 0"))
            print("Added sync_failure_count column.")
        else:
            print("Column sync_failure_count already exists, skipping.")

        # Add last_sync_error column if it doesn't exist
        if "last_sync_error" not in columns:
            print("Adding last_sync_error column...")
            if "sqlite" in str(engine.url).lower():
                conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN last_sync_error TEXT"))
            else:
                conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN last_sync_error TEXT"))
            print("Added last_sync_error column.")
        else:
            print("Column last_sync_error already exists, skipping.")

        # Set migration defaults for existing rows
        print("Setting migration defaults...")
        if "sqlite" in str(engine.url).lower():
            # SQLite: Set sync_success_count = 0 where NULL
            conn.execute(
                text("""
                    UPDATE strava_accounts
                    SET sync_success_count = 0
                    WHERE sync_success_count IS NULL
                """)
            )
            # SQLite: Set sync_failure_count = 0 where NULL
            conn.execute(
                text("""
                    UPDATE strava_accounts
                    SET sync_failure_count = 0
                    WHERE sync_failure_count IS NULL
                """)
            )
        else:
            # PostgreSQL: Set sync_success_count = 0 where NULL
            conn.execute(
                text("""
                    UPDATE strava_accounts
                    SET sync_success_count = 0
                    WHERE sync_success_count IS NULL
                """)
            )
            # PostgreSQL: Set sync_failure_count = 0 where NULL
            conn.execute(
                text("""
                    UPDATE strava_accounts
                    SET sync_failure_count = 0
                    WHERE sync_failure_count IS NULL
                """)
            )

        print("Migration complete: Added sync tracking columns to strava_accounts table.")


if __name__ == "__main__":
    migrate_strava_accounts_sync_tracking()
