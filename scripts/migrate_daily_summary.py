"""Migration script to create daily_training_summary table.

This migration creates the daily_training_summary derived table for Phase 6.
The table aggregates daily training metrics from the activities table.
"""

from loguru import logger
from sqlalchemy import text

from app.state.db import engine


def migrate_daily_summary() -> None:
    """Create daily_training_summary table if it doesn't exist."""
    logger.info("Starting daily_training_summary migration")

    # Check if table exists (using a simple connection for read-only check)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_training_summary'"))
        table_exists = result.fetchone() is not None

    if table_exists:
        logger.info("daily_training_summary table already exists, skipping migration")
        return

    logger.info("daily_training_summary table does not exist, creating it...")

    # Use engine.begin() for proper transaction handling
    with engine.begin() as conn:
        try:
            # Create table
            logger.debug("Creating daily_training_summary table structure")
            conn.execute(
                text(
                    """
                    CREATE TABLE daily_training_summary (
                        athlete_id INTEGER NOT NULL,
                        date DATE NOT NULL,
                        duration_s INTEGER NOT NULL,
                        distance_m FLOAT NOT NULL,
                        elevation_m FLOAT NOT NULL,
                        load_score FLOAT NOT NULL,
                        PRIMARY KEY (athlete_id, date)
                    )
                    """
                )
            )

            # Create index on date for efficient queries
            logger.debug("Creating index on date column")
            conn.execute(text("CREATE INDEX idx_daily_training_summary_date ON daily_training_summary (date)"))

            # Create index on athlete_id for efficient queries
            logger.debug("Creating index on athlete_id column")
            conn.execute(text("CREATE INDEX idx_daily_training_summary_athlete_id ON daily_training_summary (athlete_id)"))

            logger.info("Migration complete: Created daily_training_summary table with indexes")

        except Exception as e:
            logger.error(f"Migration failed while creating daily_training_summary table: {e}")
            raise


if __name__ == "__main__":
    migrate_daily_summary()
