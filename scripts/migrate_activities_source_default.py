"""Migration script to set default value for source column in activities table.

This migration ensures the source column has a default value of 'strava' at the database level.
"""

from loguru import logger
from sqlalchemy import text

from app.core.settings import settings
from app.state.db import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def migrate_activities_source_default() -> None:
    """Set default value for source column in activities table."""
    logger.info("Starting migration to set default value for source column")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if source column exists
            if _is_postgresql():
                result = conn.execute(
                    text(
                        """
                        SELECT column_default
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                        AND table_name = 'activities'
                        AND column_name = 'source'
                        """
                    )
                )
                column_info = result.fetchone()
                if not column_info:
                    logger.info("source column does not exist in activities table, skipping migration")
                    return

                current_default = column_info[0]
                if current_default and "'strava'" in str(current_default):
                    logger.info("source column already has default value 'strava', skipping migration")
                    return

                logger.info("Setting default value 'strava' for source column...")

                # PostgreSQL: Set default value
                conn.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ALTER COLUMN source SET DEFAULT 'strava'
                        """
                    )
                )

                # Update existing NULL values to 'strava'
                result = conn.execute(text("UPDATE activities SET source = 'strava' WHERE source IS NULL"))
                updated_count = result.rowcount
                if updated_count > 0:
                    logger.info(f"Updated {updated_count} existing activities with NULL source to 'strava'")

                logger.info("Set default value 'strava' for source column (PostgreSQL)")
            else:
                # SQLite: Default values are handled differently
                logger.warning("SQLite detected - default values are handled at application level")
                logger.info("SQLite will use the model default value")
                return

            logger.info(f"Migration complete: Set default value for source column ({db_type})")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_activities_source_default()
