"""Migration script to add effort computation fields to activities table.

This migration adds:
- normalized_power: Normalized Power (cycling) or Running Effort (running) or HR effort (Float, nullable)
- effort_source: Source of effort metric ("power", "pace", "hr") (String, nullable)
- intensity_factor: Intensity Factor (IF = NP / threshold) (Float, nullable)

Usage:
    From project root:
    python scripts/migrate_add_activity_effort_fields.py

    Or as a module:
    python -m scripts.migrate_add_activity_effort_fields
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import SessionLocal


def migrate_add_activity_effort_fields() -> None:
    """Add effort computation fields to activities table."""
    logger.info("Starting migration: add effort computation fields to activities table")

    db = SessionLocal()
    try:
        is_postgresql = "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()

        # Check if columns already exist
        if is_postgresql:
            # PostgreSQL
            result = db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'activities'
                    AND column_name IN ('normalized_power', 'effort_source', 'intensity_factor')
                    """
                )
            ).fetchall()
            existing_columns = {row[0] for row in result}
        else:
            # SQLite
            result = db.execute(text("PRAGMA table_info(activities)")).fetchall()
            existing_columns = {col[1] for col in result}

        # Add normalized_power column if missing
        if "normalized_power" not in existing_columns:
            logger.info("Adding normalized_power column to activities table")
            if is_postgresql:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN normalized_power DOUBLE PRECISION
                        """
                    )
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN normalized_power REAL
                        """
                    )
                )
            logger.info("Successfully added normalized_power column to activities table")
        else:
            logger.info("Column normalized_power already exists, skipping")

        # Add effort_source column if missing
        if "effort_source" not in existing_columns:
            logger.info("Adding effort_source column to activities table")
            if is_postgresql:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN effort_source VARCHAR
                        """
                    )
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN effort_source TEXT
                        """
                    )
                )
            logger.info("Successfully added effort_source column to activities table")
        else:
            logger.info("Column effort_source already exists, skipping")

        # Add intensity_factor column if missing
        if "intensity_factor" not in existing_columns:
            logger.info("Adding intensity_factor column to activities table")
            if is_postgresql:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN intensity_factor DOUBLE PRECISION
                        """
                    )
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN intensity_factor REAL
                        """
                    )
                )
            logger.info("Successfully added intensity_factor column to activities table")
        else:
            logger.info("Column intensity_factor already exists, skipping")

        db.commit()
        logger.info("Migration completed successfully: effort computation fields added to activities table")

    except Exception as e:
        logger.error(f"Error during migration: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_activity_effort_fields()
    logger.info("Migration completed successfully")
