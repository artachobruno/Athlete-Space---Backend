"""Migration script to add streams_data column to activities table.

This migration adds a JSON column to store time-series streams data from Strava
(GPS coordinates, heart rate, power, cadence, speed, etc.) for activities.

Usage:
    python scripts/migrate_add_streams_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path so we can import from app
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.settings import settings  # noqa: E402
from app.state.db import SessionLocal  # noqa: E402


def migrate_add_streams_data() -> None:
    """Add streams_data column to activities table."""
    logger.info("Starting migration: add streams_data column to activities table")

    db = SessionLocal()
    try:
        # Check if column already exists
        if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower():
            # PostgreSQL
            result = db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'activities' AND column_name = 'streams_data'
                    """
                )
            ).fetchone()
            column_exists = result is not None
        else:
            # SQLite
            result = db.execute(text("PRAGMA table_info(activities)")).fetchall()
            column_exists = any(col[1] == "streams_data" for col in result)

        if column_exists:
            logger.info("Column streams_data already exists, skipping migration")
            return

        # Add column
        logger.info("Adding streams_data column to activities table")
        if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower():
            # PostgreSQL: Use JSONB for better performance
            db.execute(
                text(
                    """
                    ALTER TABLE activities
                    ADD COLUMN streams_data JSONB
                    """
                )
            )
        else:
            # SQLite: Use JSON (SQLite 3.38+ supports JSON)
            db.execute(
                text(
                    """
                    ALTER TABLE activities
                    ADD COLUMN streams_data JSON
                    """
                )
            )

        db.commit()
        logger.info("Successfully added streams_data column to activities table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_streams_data()
    logger.info("Migration completed successfully")
