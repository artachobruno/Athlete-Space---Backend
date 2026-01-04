"""Migration script to drop the obsolete 'activity_id' column from activities table.

This migration removes the 'activity_id' column which is not part of the Activity model.
The Activity model uses 'id' (UUID) as the primary key and 'strava_activity_id' for the Strava ID.
"""

from loguru import logger
from sqlalchemy import text

from app.core.settings import settings
from app.state.db import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def migrate_drop_activity_id() -> None:
    """Drop activity_id column from activities table if it exists."""
    logger.info("Starting migration to drop activity_id column from activities table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if activity_id column exists
            if _is_postgresql():
                result = conn.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT FROM information_schema.columns
                            WHERE table_schema = 'public'
                            AND table_name = 'activities'
                            AND column_name = 'activity_id'
                        )
                        """
                    )
                )
                column_exists = result.scalar() is True
            else:
                result = conn.execute(text("PRAGMA table_info(activities)"))
                columns = [row[1] for row in result.fetchall()]
                column_exists = "activity_id" in columns

            if not column_exists:
                logger.info("activity_id column does not exist in activities table, skipping migration")
                return

            logger.info("Dropping activity_id column from activities table...")

            if _is_postgresql():
                # PostgreSQL: Drop the column directly
                conn.execute(
                    text(
                        """
                        ALTER TABLE activities
                        DROP COLUMN activity_id
                        """
                    )
                )
                logger.info("Dropped activity_id column from activities table (PostgreSQL)")
            else:
                # SQLite: Need to recreate table without the column
                logger.warning("SQLite detected - dropping column requires table recreation")
                logger.info("For SQLite, you may need to recreate the activities table manually")
                logger.info("Or use a tool like sqlite3 to recreate the table")
                return

            logger.info(f"Migration complete: Dropped activity_id column from activities table ({db_type})")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_drop_activity_id()
