# ⚠️ LEGACY: One-time migration script - not included in run_migrations.py
# Keep for historical reference and potential database recovery scenarios
"""Migration script to add sync tracking columns to strava_auth table.

This migration adds:
- last_successful_sync_at: Timestamp of last successful incremental sync
- backfill_updated_at: Timestamp of last backfill progress update

These columns enable SLA monitoring and auto-healing of stuck backfills.
"""

from sqlalchemy import text

from app.db.models import Base
from app.db.session import get_engine


def migrate_sync_tracking() -> None:
    """Add sync tracking columns to strava_auth table."""
    engine = get_engine()
    with engine.connect() as conn:
        # Check if table exists
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='strava_auth'"))
        table_exists = result.fetchone() is not None

        if not table_exists:
            print("strava_auth table does not exist. Creating new table...")
            # Table doesn't exist - create it fresh using SQLAlchemy models
            Base.metadata.create_all(bind=get_engine())
            print("Migration complete: Created new strava_auth table with sync tracking columns.")
            return

        # Check existing columns
        result = conn.execute(text("PRAGMA table_info(strava_auth)"))
        columns = {row[1]: row[2] for row in result.fetchall()}

        # Start transaction
        trans = conn.begin()

        try:
            # Add last_successful_sync_at if it doesn't exist
            if "last_successful_sync_at" not in columns:
                conn.execute(text("ALTER TABLE strava_auth ADD COLUMN last_successful_sync_at INTEGER"))
                print("Added column: last_successful_sync_at")
            else:
                print("Column last_successful_sync_at already exists, skipping.")

            # Add backfill_updated_at if it doesn't exist
            if "backfill_updated_at" not in columns:
                conn.execute(text("ALTER TABLE strava_auth ADD COLUMN backfill_updated_at INTEGER"))
                print("Added column: backfill_updated_at")
            else:
                print("Column backfill_updated_at already exists, skipping.")

            trans.commit()
            print("Migration complete: Sync tracking columns added to strava_auth table.")

        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_sync_tracking()
