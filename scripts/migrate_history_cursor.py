"""Migration script to add history cursor fields to strava_accounts table.

This migration adds:
- oldest_synced_at: Earliest activity timestamp synced (nullable integer)
- full_history_synced: Whether full history backfill is complete (boolean, default False)

Migration defaults:
- If last_sync_at exists, set oldest_synced_at = last_sync_at
- Set full_history_synced = False for all accounts
"""

from sqlalchemy import text

from app.state.db import engine


def migrate_history_cursor() -> None:
    """Add history cursor fields to strava_accounts table."""
    with engine.connect() as conn:
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
                    WHERE table_name = 'strava_accounts'
                """)
            )
            columns = [row[0] for row in result.fetchall()]

        trans = conn.begin()

        try:
            # Add oldest_synced_at column if it doesn't exist
            if "oldest_synced_at" not in columns:
                print("Adding oldest_synced_at column...")
                if "sqlite" in str(engine.url).lower():
                    conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN oldest_synced_at INTEGER"))
                else:
                    conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN oldest_synced_at INTEGER"))
                print("Added oldest_synced_at column.")

            # Add full_history_synced column if it doesn't exist
            if "full_history_synced" not in columns:
                print("Adding full_history_synced column...")
                if "sqlite" in str(engine.url).lower():
                    conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN full_history_synced BOOLEAN DEFAULT 0"))
                else:
                    conn.execute(text("ALTER TABLE strava_accounts ADD COLUMN full_history_synced BOOLEAN DEFAULT FALSE"))
                print("Added full_history_synced column.")

            # Set migration defaults
            print("Setting migration defaults...")
            if "sqlite" in str(engine.url).lower():
                # SQLite: Update oldest_synced_at from last_sync_at where oldest_synced_at is NULL
                conn.execute(
                    text("""
                        UPDATE strava_accounts
                        SET oldest_synced_at = last_sync_at
                        WHERE oldest_synced_at IS NULL AND last_sync_at IS NOT NULL
                    """)
                )
                # SQLite: Set full_history_synced = 0 (False) where NULL
                conn.execute(
                    text("""
                        UPDATE strava_accounts
                        SET full_history_synced = 0
                        WHERE full_history_synced IS NULL
                    """)
                )
            else:
                # PostgreSQL
                conn.execute(
                    text("""
                        UPDATE strava_accounts
                        SET oldest_synced_at = last_sync_at
                        WHERE oldest_synced_at IS NULL AND last_sync_at IS NOT NULL
                    """)
                )
                conn.execute(
                    text("""
                        UPDATE strava_accounts
                        SET full_history_synced = FALSE
                        WHERE full_history_synced IS NULL
                    """)
                )

            trans.commit()
            print("Migration complete: Added history cursor fields to strava_accounts table.")
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_history_cursor()
