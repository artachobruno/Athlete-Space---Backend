"""Migration script to create daily_training_summary table.

This migration creates the daily_training_summary derived table for Phase 6.
The table aggregates daily training metrics from the activities table.

Supports both SQLite and PostgreSQL.
"""

from loguru import logger
from sqlalchemy import text

from app.core.settings import settings
from app.state.db import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _table_exists(conn, table_name: str) -> bool:
    """Check if table exists (database-agnostic)."""
    if _is_postgresql():
        # PostgreSQL
        result = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return result.scalar() is True
    # SQLite
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def migrate_daily_summary() -> None:
    """Create daily_training_summary table if it doesn't exist.

    Supports both SQLite and PostgreSQL databases.
    """
    logger.info("Starting daily_training_summary migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    # Check if table exists
    with engine.connect() as conn:
        table_exists = _table_exists(conn, "daily_training_summary")

    if table_exists:
        logger.info("daily_training_summary table already exists, skipping migration")
        return

    logger.info("daily_training_summary table does not exist, creating it...")

    # Use engine.begin() for proper transaction handling
    with engine.begin() as conn:
        try:
            # Create table (works for both SQLite and PostgreSQL)
            logger.debug("Creating daily_training_summary table structure")

            # Use REAL for SQLite, DOUBLE PRECISION for PostgreSQL
            float_type = "DOUBLE PRECISION" if _is_postgresql() else "REAL"

            conn.execute(
                text(
                    f"""
                    CREATE TABLE daily_training_summary (
                        athlete_id INTEGER NOT NULL,
                        date DATE NOT NULL,
                        duration_s INTEGER NOT NULL,
                        distance_m {float_type} NOT NULL,
                        elevation_m {float_type} NOT NULL,
                        load_score {float_type} NOT NULL,
                        PRIMARY KEY (athlete_id, date)
                    )
                    """
                )
            )

            # Create indexes (IF NOT EXISTS for PostgreSQL compatibility)
            logger.debug("Creating indexes")
            if _is_postgresql():
                # PostgreSQL: use IF NOT EXISTS
                conn.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_daily_training_summary_date
                        ON daily_training_summary (date)
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_daily_training_summary_athlete_id
                        ON daily_training_summary (athlete_id)
                        """
                    )
                )
            else:
                # SQLite: indexes are created directly
                conn.execute(text("CREATE INDEX idx_daily_training_summary_date ON daily_training_summary (date)"))
                conn.execute(text("CREATE INDEX idx_daily_training_summary_athlete_id ON daily_training_summary (athlete_id)"))

            logger.info(f"Migration complete: Created daily_training_summary table with indexes ({db_type})")

        except Exception as e:
            logger.error(f"Migration failed while creating daily_training_summary table: {e}")
            raise


if __name__ == "__main__":
    migrate_daily_summary()
