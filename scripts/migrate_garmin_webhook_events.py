"""Migration script to create garmin_webhook_events table."""

from sqlalchemy import text

from app.db.models import Base
from app.db.session import get_engine


def migrate_garmin_webhook_events() -> None:
    """Create garmin_webhook_events table if it doesn't exist."""
    engine = get_engine()
    with engine.connect() as conn:
        # Check if table exists
        if "sqlite" in str(engine.url).lower():
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='garmin_webhook_events'"))
        else:
            # PostgreSQL
            result = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename = 'garmin_webhook_events'"
                )
            )
        table_exists = result.fetchone() is not None

        if table_exists:
            print("garmin_webhook_events table already exists. Skipping migration.")
            return

        print("Creating garmin_webhook_events table...")

        # Start transaction
        trans = conn.begin()

        try:
            # Create table using SQLAlchemy metadata
            Base.metadata.create_all(bind=get_engine(), tables=[Base.metadata.tables["garmin_webhook_events"]])

            trans.commit()
            print("Migration complete: Created garmin_webhook_events table.")
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_garmin_webhook_events()
