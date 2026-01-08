"""Migration script to add tss and tss_version columns to activities table.

This migration adds:
- tss: Training Stress Score (Float, nullable)
- tss_version: Version identifier for TSS computation method (String, nullable)

Usage:
    From project root:
    python scripts/migrate_add_activity_tss.py

    Or as a module:
    python -m scripts.migrate_add_activity_tss
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


def migrate_add_activity_tss() -> None:
    """Add tss and tss_version columns to activities table."""
    logger.info("Starting migration: add tss and tss_version columns to activities table")

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
                    AND column_name IN ('tss', 'tss_version')
                    """
                )
            ).fetchall()
            existing_columns = {row[0] for row in result}
        else:
            # SQLite
            result = db.execute(text("PRAGMA table_info(activities)")).fetchall()
            existing_columns = {col[1] for col in result}

        # Add tss column if missing
        if "tss" not in existing_columns:
            logger.info("Adding tss column to activities table")
            if is_postgresql:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN tss DOUBLE PRECISION
                        """
                    )
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN tss REAL
                        """
                    )
                )
            logger.info("Successfully added tss column to activities table")
        else:
            logger.info("Column tss already exists, skipping")

        # Add tss_version column if missing
        if "tss_version" not in existing_columns:
            logger.info("Adding tss_version column to activities table")
            if is_postgresql:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN tss_version VARCHAR
                        """
                    )
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN tss_version TEXT
                        """
                    )
                )
            logger.info("Successfully added tss_version column to activities table")
        else:
            logger.info("Column tss_version already exists, skipping")

        db.commit()
        logger.info("Migration completed successfully: tss and tss_version columns added to activities table")

    except Exception as e:
        logger.error(f"Error during migration: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_activity_tss()
    logger.info("Migration completed successfully")
