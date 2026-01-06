"""Migration script to add user_id column to daily_training_summary table.

This migration adds user_id column and migrates from athlete_id-based schema
to user_id-based schema for multi-user support.
"""

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if column exists in table (database-agnostic)."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                    AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.scalar() is True
    # SQLite
    result = conn.execute(
        text("PRAGMA table_info(:table_name)"),
        {"table_name": table_name},
    )
    columns = result.fetchall()
    return any(col[1] == column_name for col in columns)


def migrate_daily_summary_user_id() -> None:
    """Add user_id column to daily_training_summary table and update schema.

    This migration:
    1. Adds user_id column (STRING)
    2. Updates primary key to (user_id, date)
    3. Creates index on user_id
    4. Drops old athlete_id index and column (if they exist)
    """
    logger.info("Starting daily_training_summary user_id migration")

    with engine.begin() as conn:
        # Check if user_id column already exists
        if _column_exists(conn, "daily_training_summary", "user_id"):
            logger.info("user_id column already exists in daily_training_summary, skipping migration")
            return

        logger.info("Adding user_id column to daily_training_summary table")

        if _is_postgresql():
            # PostgreSQL: Recreate table with user_id schema (drop old data)
            logger.info("PostgreSQL detected - recreating table with user_id schema (old data will be dropped)")

            # Create new table with user_id
            conn.execute(
                text(
                    """
                    CREATE TABLE daily_training_summary_new (
                        user_id VARCHAR NOT NULL,
                        date DATE NOT NULL,
                        duration_s INTEGER NOT NULL,
                        distance_m DOUBLE PRECISION NOT NULL,
                        elevation_m DOUBLE PRECISION NOT NULL,
                        load_score DOUBLE PRECISION NOT NULL,
                        PRIMARY KEY (user_id, date)
                    )
                    """
                )
            )

            # Drop old table
            conn.execute(text("DROP TABLE IF EXISTS daily_training_summary"))

            # Rename new table
            conn.execute(text("ALTER TABLE daily_training_summary_new RENAME TO daily_training_summary"))

            # Create indexes
            conn.execute(
                text(
                    """
                    CREATE INDEX idx_daily_training_summary_date
                    ON daily_training_summary (date)
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX idx_daily_training_summary_user_id
                    ON daily_training_summary (user_id)
                    """
                )
            )
        else:
            # SQLite: Need to recreate table (SQLite doesn't support ALTER COLUMN well)
            logger.info("SQLite detected - recreating table with user_id schema")

            # Create new table with user_id
            conn.execute(
                text(
                    """
                    CREATE TABLE daily_training_summary_new (
                        user_id TEXT NOT NULL,
                        date DATE NOT NULL,
                        duration_s INTEGER NOT NULL,
                        distance_m REAL NOT NULL,
                        elevation_m REAL NOT NULL,
                        load_score REAL NOT NULL,
                        PRIMARY KEY (user_id, date)
                    )
                    """
                )
            )

            # Copy data (if any exists, though it will be empty for new schema)
            # Note: Old data keyed by athlete_id cannot be migrated without mapping
            # So we just create the new empty table structure
            conn.execute(text("DROP TABLE IF EXISTS daily_training_summary"))
            conn.execute(text("ALTER TABLE daily_training_summary_new RENAME TO daily_training_summary"))

            # Create indexes
            conn.execute(text("CREATE INDEX idx_daily_training_summary_date ON daily_training_summary (date)"))
            conn.execute(text("CREATE INDEX idx_daily_training_summary_user_id ON daily_training_summary (user_id)"))

        logger.info("Migration complete: Added user_id column to daily_training_summary table")


if __name__ == "__main__":
    migrate_daily_summary_user_id()
