"""Migration script to add athlete_id column to activities table.

This migration adds the athlete_id column to support multi-user persistence.
For existing activities without athlete_id, it attempts to infer from StravaAuth.
"""

from loguru import logger
from sqlalchemy import text

from app.core.settings import settings
from app.state.db import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def migrate_add_athlete_id() -> None:
    """Add athlete_id column to activities table if it doesn't exist.

    For existing activities, attempts to set athlete_id from StravaAuth.
    """
    logger.info("Starting athlete_id migration for activities table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if athlete_id column exists
            if _is_postgresql():
                result = conn.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT FROM information_schema.columns
                            WHERE table_schema = 'public'
                            AND table_name = 'activities'
                            AND column_name = 'athlete_id'
                        )
                        """
                    )
                )
                column_exists = result.scalar() is True
            else:
                result = conn.execute(text("PRAGMA table_info(activities)"))
                columns = [row[1] for row in result.fetchall()]
                column_exists = "athlete_id" in columns

            if column_exists:
                logger.info("athlete_id column already exists in activities table, skipping migration")
                return

            logger.info("Adding athlete_id column to activities table...")

            # Add athlete_id column (nullable initially)
            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN athlete_id INTEGER
                        """
                    )
                )
            else:
                # SQLite doesn't support ALTER TABLE ADD COLUMN directly
                # We'll need to recreate the table
                logger.warning("SQLite detected - athlete_id migration requires table recreation")
                logger.info("For SQLite, you may need to recreate the activities table manually")
                logger.info("Or use a tool like sqlite3 to add the column")
                return

            # Create index on athlete_id
            logger.debug("Creating index on athlete_id")
            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_activities_athlete_id
                        ON activities (athlete_id)
                        """
                    )
                )

            # Try to populate athlete_id from StravaAuth for existing activities
            logger.info("Attempting to populate athlete_id for existing activities...")
            if _is_postgresql():
                # Get the first athlete_id from StravaAuth (for single-user systems)
                result = conn.execute(text("SELECT athlete_id FROM strava_auth LIMIT 1"))
                first_athlete = result.fetchone()

                if first_athlete:
                    athlete_id = first_athlete[0]
                    logger.info(f"Found athlete_id={athlete_id}, updating existing activities...")
                    conn.execute(
                        text("UPDATE activities SET athlete_id = :athlete_id WHERE athlete_id IS NULL"), {"athlete_id": athlete_id}
                    )
                    logger.info("Updated existing activities with athlete_id")
                else:
                    logger.warning("No StravaAuth records found - cannot auto-populate athlete_id")

            # Make athlete_id NOT NULL after population
            logger.debug("Making athlete_id NOT NULL...")
            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ALTER COLUMN athlete_id SET NOT NULL
                        """
                    )
                )

            logger.info(f"Migration complete: Added athlete_id column to activities table ({db_type})")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_add_athlete_id()
