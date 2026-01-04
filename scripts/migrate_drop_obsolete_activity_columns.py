"""Migration script to drop obsolete columns from activities table.

This migration removes old column names that don't match the Activity model:
- sport (replaced by 'type')
- avg_hr (data available in raw_json)
- distance_m (replaced by 'distance_meters')
- elevation_m (replaced by 'elevation_gain_meters')
- duration_s (replaced by 'duration_seconds')
- activity_id (already handled by migrate_drop_activity_id)
"""

from loguru import logger
from sqlalchemy import text

from app.core.settings import settings
from app.state.db import engine

# Columns to drop (old names that don't match Activity model)
OBSOLETE_COLUMNS = ["sport", "avg_hr", "distance_m", "elevation_m", "duration_s"]


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def migrate_drop_obsolete_activity_columns() -> None:
    """Drop obsolete columns from activities table."""
    logger.info("Starting migration to drop obsolete columns from activities table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Get existing columns
            if _is_postgresql():
                result = conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                        AND table_name = 'activities'
                        """
                    )
                )
                existing_columns = {row[0] for row in result.fetchall()}
            else:
                result = conn.execute(text("PRAGMA table_info(activities)"))
                existing_columns = {row[1] for row in result.fetchall()}

            logger.info(f"Existing columns in activities table: {sorted(existing_columns)}")

            # Drop obsolete columns that exist
            dropped_count = 0
            for column_name in OBSOLETE_COLUMNS:
                if column_name not in existing_columns:
                    logger.debug(f"Column '{column_name}' does not exist, skipping")
                    continue

                logger.info(f"Dropping obsolete column '{column_name}' from activities table...")

                if _is_postgresql():
                    conn.execute(
                        text(
                            f"""
                            ALTER TABLE activities
                            DROP COLUMN IF EXISTS {column_name}
                            """
                        )
                    )
                    logger.info(f"Dropped column '{column_name}' (PostgreSQL)")
                else:
                    logger.warning(f"SQLite detected - dropping column '{column_name}' requires table recreation")
                    logger.info("For SQLite, you may need to recreate the activities table manually")
                    continue

                dropped_count += 1

            if dropped_count == 0:
                logger.info("No obsolete columns found to drop")
            else:
                logger.info(f"Migration complete: Dropped {dropped_count} obsolete column(s) ({db_type})")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_drop_obsolete_activity_columns()
