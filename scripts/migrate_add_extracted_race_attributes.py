# ⚠️ LEGACY: One-time migration script - not included in run_migrations.py
# Keep for historical reference and potential database recovery scenarios
"""Migration script to add extracted_race_attributes column to athlete_profiles table.

This migration adds a JSON column to store extracted race attributes from goal extraction.

Usage:
    From project root:
    python scripts/migrate_add_extracted_race_attributes.py

    Or as a module:
    python -m scripts.migrate_add_extracted_race_attributes
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import SessionLocal


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _column_exists(db, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table.

    Args:
        db: Database session
        table_name: Name of the table
        column_name: Name of the column

    Returns:
        True if column exists, False otherwise
    """
    if _is_postgresql():
        result = db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = :column_name
                """,
            ),
            {"table_name": table_name, "column_name": column_name},
        ).fetchone()
        return result is not None
    result = db.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return any(col[1] == column_name for col in result)


def migrate_add_extracted_race_attributes() -> None:
    """Add extracted_race_attributes column to athlete_profiles table."""
    logger.info("Starting migration: add extracted_race_attributes column to athlete_profiles table")

    db = SessionLocal()
    try:
        if _column_exists(db, "athlete_profiles", "extracted_race_attributes"):
            logger.info("Column extracted_race_attributes already exists, skipping migration")
            return

        logger.info("Adding extracted_race_attributes column to athlete_profiles table")
        if _is_postgresql():
            db.execute(
                text(
                    """
                    ALTER TABLE athlete_profiles
                    ADD COLUMN extracted_race_attributes JSONB
                    """,
                ),
            )
        else:
            db.execute(
                text(
                    """
                    ALTER TABLE athlete_profiles
                    ADD COLUMN extracted_race_attributes JSON
                    """,
                ),
            )

        db.commit()
        logger.info("Successfully added extracted_race_attributes column to athlete_profiles table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_extracted_race_attributes()
    logger.info("Migration completed successfully")
