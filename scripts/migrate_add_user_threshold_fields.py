"""Migration script to add threshold configuration fields to user_settings table.

This migration adds:
- ftp_watts: Functional Threshold Power for cycling (Float, nullable)
- threshold_pace_ms: Threshold pace for running in m/s (Float, nullable)
- threshold_hr: Threshold heart rate in bpm (Integer, nullable)

Usage:
    From project root:
    python scripts/migrate_add_user_threshold_fields.py

    Or as a module:
    python -m scripts.migrate_add_user_threshold_fields
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


def migrate_add_user_threshold_fields() -> None:
    """Add threshold configuration fields to user_settings table.

    This migration is idempotent: it checks if columns exist before adding them.
    Safe to run multiple times, including concurrent runs (PostgreSQL handles DDL locks).

    Production note: After first successful run, consider removing from startup
    and running migrations separately to avoid coupling schema evolution to web lifecycle.
    """
    logger.info("Starting migration: add threshold configuration fields to user_settings table")

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
                    WHERE table_name = 'user_settings'
                    AND column_name IN ('ftp_watts', 'threshold_pace_ms', 'threshold_hr')
                    """
                )
            ).fetchall()
            existing_columns = {row[0] for row in result}
        else:
            # SQLite
            result = db.execute(text("PRAGMA table_info(user_settings)")).fetchall()
            existing_columns = {col[1] for col in result}

        # Add ftp_watts column if missing
        if "ftp_watts" not in existing_columns:
            logger.info("Adding ftp_watts column to user_settings table")
            if is_postgresql:
                db.execute(
                    text(
                        """
                        ALTER TABLE user_settings
                        ADD COLUMN ftp_watts DOUBLE PRECISION
                        """
                    )
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE user_settings
                        ADD COLUMN ftp_watts REAL
                        """
                    )
                )
            logger.info("Successfully added ftp_watts column to user_settings table")
        else:
            logger.info("Column ftp_watts already exists, skipping")

        # Add threshold_pace_ms column if missing
        if "threshold_pace_ms" not in existing_columns:
            logger.info("Adding threshold_pace_ms column to user_settings table")
            if is_postgresql:
                db.execute(
                    text(
                        """
                        ALTER TABLE user_settings
                        ADD COLUMN threshold_pace_ms DOUBLE PRECISION
                        """
                    )
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE user_settings
                        ADD COLUMN threshold_pace_ms REAL
                        """
                    )
                )
            logger.info("Successfully added threshold_pace_ms column to user_settings table")
        else:
            logger.info("Column threshold_pace_ms already exists, skipping")

        # Add threshold_hr column if missing
        if "threshold_hr" not in existing_columns:
            logger.info("Adding threshold_hr column to user_settings table")
            if is_postgresql:
                db.execute(
                    text(
                        """
                        ALTER TABLE user_settings
                        ADD COLUMN threshold_hr INTEGER
                        """
                    )
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE user_settings
                        ADD COLUMN threshold_hr INTEGER
                        """
                    )
                )
            logger.info("Successfully added threshold_hr column to user_settings table")
        else:
            logger.info("Column threshold_hr already exists, skipping")

        db.commit()
        logger.info("Migration completed successfully: threshold configuration fields added to user_settings table")

    except Exception as e:
        logger.error(f"Error during migration: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_user_threshold_fields()
    logger.info("Migration completed successfully")
