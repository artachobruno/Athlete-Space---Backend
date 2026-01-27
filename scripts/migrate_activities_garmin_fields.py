"""Migration script to add Garmin-specific fields to activities table.

Adds:
- source_provider: Provider name (e.g., 'garmin', 'strava')
- external_activity_id: External activity ID from provider
- unique constraint on (source_provider, external_activity_id) for idempotent ingestion
"""

from sqlalchemy import text

from app.db.session import get_engine


def migrate_activities_garmin_fields() -> None:
    """Add source_provider and external_activity_id fields to activities table."""
    engine = get_engine()
    with engine.connect() as conn:
        # Check if columns already exist
        if "sqlite" in str(engine.url).lower():
            result = conn.execute(text("PRAGMA table_info(activities)"))
            columns = {row[1] for row in result.fetchall()}
        else:
            # PostgreSQL
            result = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'activities' AND table_schema = 'public'"
                )
            )
            columns = {row[0] for row in result.fetchall()}

        trans = conn.begin()

        try:
            # Add source_provider column if it doesn't exist
            if "source_provider" not in columns:
                print("Adding source_provider column to activities table...")
                if "sqlite" in str(engine.url).lower():
                    conn.execute(text("ALTER TABLE activities ADD COLUMN source_provider TEXT"))
                else:
                    conn.execute(text("ALTER TABLE activities ADD COLUMN source_provider VARCHAR"))
                print("Added source_provider column.")
            else:
                print("source_provider column already exists.")

            # Add external_activity_id column if it doesn't exist
            if "external_activity_id" not in columns:
                print("Adding external_activity_id column to activities table...")
                if "sqlite" in str(engine.url).lower():
                    conn.execute(text("ALTER TABLE activities ADD COLUMN external_activity_id TEXT"))
                else:
                    conn.execute(text("ALTER TABLE activities ADD COLUMN external_activity_id VARCHAR"))
                print("Added external_activity_id column.")
            else:
                print("external_activity_id column already exists.")

            # Check if unique constraint already exists
            if "sqlite" in str(engine.url).lower():
                # SQLite doesn't support named constraints easily, skip for now
                print("Skipping unique constraint creation for SQLite (use PostgreSQL in production).")
            else:
                # Check if constraint exists
                result = conn.execute(
                    text(
                        "SELECT constraint_name FROM information_schema.table_constraints "
                        "WHERE table_name = 'activities' AND constraint_name = 'uq_activity_source_provider_external_id'"
                    )
                )
                constraint_exists = result.fetchone() is not None

                if not constraint_exists:
                    print("Creating unique constraint on (source_provider, external_activity_id)...")
                    conn.execute(
                        text(
                            "ALTER TABLE activities "
                            "ADD CONSTRAINT uq_activity_source_provider_external_id "
                            "UNIQUE (source_provider, external_activity_id)"
                        )
                    )
                    print("Created unique constraint.")
                else:
                    print("Unique constraint already exists.")

            trans.commit()
            print("Migration complete: Added Garmin fields to activities table.")
        except Exception as e:
            trans.rollback()
            print(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_activities_garmin_fields()
