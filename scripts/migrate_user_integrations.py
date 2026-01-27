"""Migration script to create user_integrations table.

This migration creates the user_integrations table with:
- id (uuid, pk)
- user_id (fk)
- provider = 'garmin'
- provider_user_id
- access_token (encrypted)
- refresh_token (encrypted)
- token_expires_at
- scopes (jsonb)
- connected_at
- revoked_at
- last_sync_at
- unique (user_id, provider)
- index (provider, provider_user_id)
"""

from sqlalchemy import text

from app.db.models import Base
from app.db.session import get_engine


def migrate_user_integrations() -> None:
    """Create user_integrations table if it doesn't exist."""
    engine = get_engine()
    with engine.connect() as conn:
        # Check if table exists
        if "sqlite" in str(engine.url).lower():
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='user_integrations'"))
        else:
            # PostgreSQL
            result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = 'user_integrations'"))
        table_exists = result.fetchone() is not None

        if table_exists:
            print("user_integrations table already exists. Skipping migration.")
            return

        print("Creating user_integrations table...")

        # Start transaction
        trans = conn.begin()

        try:
            # Create table using SQLAlchemy metadata
            Base.metadata.create_all(bind=get_engine(), tables=[Base.metadata.tables["user_integrations"]])

            trans.commit()
            print("Migration complete: Created user_integrations table.")
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_user_integrations()
