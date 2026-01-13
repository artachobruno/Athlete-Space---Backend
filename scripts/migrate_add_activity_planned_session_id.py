"""Migration script to add planned_session_id column to activities table.

This migration adds the pairing relationship field:
- planned_session_id: Foreign key to planned_sessions.id (nullable)

Usage:
    From project root:
    python scripts/migrate_add_activity_planned_session_id.py

    Or as a module:
    python -m scripts.migrate_add_activity_planned_session_id
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


def migrate_add_activity_planned_session_id() -> None:
    """Add planned_session_id column to activities table."""
    logger.info("Starting migration: add planned_session_id column to activities table")

    db = SessionLocal()
    try:
        if _column_exists(db, "activities", "planned_session_id"):
            logger.info("Column planned_session_id already exists, skipping")
        else:
            logger.info("Adding planned_session_id column to activities table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN planned_session_id VARCHAR
                        """,
                    ),
                )
                # Add foreign key constraint
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD CONSTRAINT fk_activities_planned_session_id
                        FOREIGN KEY (planned_session_id) REFERENCES planned_sessions(id)
                        """,
                    ),
                )
                # Add index for performance
                db.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_activities_planned_session_id
                        ON activities(planned_session_id)
                        """,
                    ),
                )
            else:
                # SQLite doesn't support adding foreign key constraints via ALTER TABLE
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN planned_session_id VARCHAR
                        """,
                    ),
                )
                # SQLite foreign keys are enforced at application level
                logger.info("Column added (SQLite foreign key enforcement at application level)")

        db.commit()
        logger.info("Successfully added planned_session_id column to activities table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_activity_planned_session_id()
    logger.info("Migration completed successfully")
