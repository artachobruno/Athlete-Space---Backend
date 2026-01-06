"""Migration script to convert athlete_id column from INTEGER to TEXT in activities table.

This migration converts athlete_id to match the format used in StravaAccount (string),
avoiding type conversions and keeping data formats consistent.
"""

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def migrate_athlete_id_to_string() -> None:
    """Convert athlete_id column from INTEGER to TEXT in activities table."""
    logger.info("Starting migration to convert athlete_id from INTEGER to TEXT")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if athlete_id column exists
            if _is_postgresql():
                result = conn.execute(
                    text(
                        """
                        SELECT data_type
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                        AND table_name = 'activities'
                        AND column_name = 'athlete_id'
                        """
                    )
                )
                column_info = result.fetchone()
                if not column_info:
                    logger.info("athlete_id column does not exist in activities table, skipping migration")
                    return

                current_type = column_info[0]
                if current_type in {"text", "varchar", "character varying"}:
                    logger.info("athlete_id column is already TEXT/VARCHAR, skipping migration")
                    return

                logger.info(f"Converting athlete_id from {current_type} to TEXT...")

                # PostgreSQL: Convert INTEGER to TEXT
                # First, convert existing integer values to text
                conn.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ALTER COLUMN athlete_id TYPE TEXT USING athlete_id::TEXT
                        """
                    )
                )
                logger.info("Converted athlete_id column to TEXT (PostgreSQL)")
            else:
                # SQLite: INTEGER can be stored as TEXT without explicit conversion
                # SQLite is type-flexible, but we should still update the schema
                logger.warning("SQLite detected - athlete_id will be stored as TEXT")
                logger.info("SQLite handles type conversion automatically, no explicit migration needed")
                return

            logger.info(f"Migration complete: Converted athlete_id to TEXT ({db_type})")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_athlete_id_to_string()
