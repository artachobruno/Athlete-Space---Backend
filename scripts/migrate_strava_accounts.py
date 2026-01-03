"""Migration script to create strava_accounts table.

This migration creates the strava_accounts table with:
- user_id (FK to users.id, primary key)
- athlete_id (string, indexed)
- access_token (encrypted string)
- refresh_token (encrypted string)
- expires_at (integer)
- last_sync_at (nullable integer)
- created_at (datetime)
"""

from sqlalchemy import text

from app.state.db import engine
from app.state.models import Base


def migrate_strava_accounts() -> None:
    """Create strava_accounts table if it doesn't exist."""
    with engine.connect() as conn:
        # Check if table exists
        if "sqlite" in str(engine.url).lower():
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='strava_accounts'"))
        else:
            # PostgreSQL
            result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'strava_accounts'"))
        table_exists = result.fetchone() is not None

        if table_exists:
            print("strava_accounts table already exists. Skipping migration.")
            return

        print("Creating strava_accounts table...")

        # Start transaction
        trans = conn.begin()

        try:
            # Create table using SQLAlchemy metadata
            Base.metadata.create_all(bind=engine, tables=[Base.metadata.tables["strava_accounts"]])

            trans.commit()
            print("Migration complete: Created strava_accounts table.")
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_strava_accounts()
