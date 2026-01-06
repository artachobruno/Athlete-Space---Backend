# ⚠️ LEGACY: One-time migration script - not included in run_migrations.py
# Keep for historical reference and potential database recovery scenarios
"""Migration script to remove access_token from strava_auth table.

This migration:
1. Creates a new table with the correct schema (no access_token)
2. Migrates existing data (athlete_id, refresh_token, expires_at)
3. Drops the old table
4. Renames the new table

This preserves existing refresh tokens while removing access tokens
(which should not be persisted).
"""

from sqlalchemy import text

from app.db.models import Base
from app.db.session import engine


def migrate_tokens() -> None:
    """Migrate strava_auth table to remove access_token column."""
    with engine.connect() as conn:
        # Check if table exists
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='strava_auth'"))
        table_exists = result.fetchone() is not None

        if not table_exists:
            print("strava_auth table does not exist. Creating new table...")
            # Table doesn't exist - create it fresh

            Base.metadata.create_all(bind=engine)
            print("Migration complete: Created new strava_auth table.")
            return

        # Check if access_token column exists
        result = conn.execute(text("PRAGMA table_info(strava_auth)"))
        columns = {row[1]: row[2] for row in result.fetchall()}

        if "access_token" not in columns:
            print("access_token column does not exist. Schema is already correct.")
            return

        print("Migrating strava_auth table...")

        # Start transaction
        trans = conn.begin()

        try:
            # Create new table with correct schema
            conn.execute(
                text(
                    """
                    CREATE TABLE strava_auth_new (
                        athlete_id INTEGER NOT NULL PRIMARY KEY,
                        refresh_token VARCHAR NOT NULL,
                        expires_at INTEGER NOT NULL
                    )
                    """
                )
            )

            # Copy data (excluding access_token)
            conn.execute(
                text(
                    """
                    INSERT INTO strava_auth_new (athlete_id, refresh_token, expires_at)
                    SELECT athlete_id, refresh_token, expires_at
                    FROM strava_auth
                    """
                )
            )

            # Drop old table
            conn.execute(text("DROP TABLE strava_auth"))

            # Rename new table
            conn.execute(text("ALTER TABLE strava_auth_new RENAME TO strava_auth"))

            # Recreate index
            conn.execute(text("CREATE INDEX ix_strava_auth_athlete_id ON strava_auth (athlete_id)"))

            trans.commit()
            print("Migration complete: Removed access_token column from strava_auth table.")
            print("Existing refresh tokens have been preserved.")

        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_tokens()
