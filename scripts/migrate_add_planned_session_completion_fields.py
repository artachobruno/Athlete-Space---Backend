"""Migration script to add completion tracking columns to planned_sessions table.

This migration adds completion tracking fields:
- completed: Boolean flag indicating if session is completed
- completed_at: Timestamp when session was completed
- completed_activity_id: Link to actual Activity if completed

Usage:
    From project root:
    python scripts/migrate_add_planned_session_completion_fields.py

    Or as a module:
    python -m scripts.migrate_add_planned_session_completion_fields
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


def migrate_add_planned_session_completion_fields() -> None:
    """Add completion tracking columns to planned_sessions table."""
    logger.info("Starting migration: add completion tracking columns to planned_sessions table")

    db = SessionLocal()
    try:
        # Add completed column
        if _column_exists(db, "planned_sessions", "completed"):
            logger.info("Column completed already exists, skipping")
        else:
            logger.info("Adding completed column to planned_sessions table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN completed BOOLEAN NOT NULL DEFAULT FALSE
                        """,
                    ),
                )
            else:
                # SQLite doesn't support adding NOT NULL columns with defaults directly
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN completed BOOLEAN
                        """,
                    ),
                )
                # Set default value for existing rows
                db.execute(
                    text(
                        """
                        UPDATE planned_sessions
                        SET completed = 0
                        WHERE completed IS NULL
                        """,
                    ),
                )
                logger.info("Completed column added (nullable in SQLite, application will enforce default)")

        # Add completed_at column
        if _column_exists(db, "planned_sessions", "completed_at"):
            logger.info("Column completed_at already exists, skipping")
        else:
            logger.info("Adding completed_at column to planned_sessions table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN completed_at TIMESTAMP
                        """,
                    ),
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN completed_at TIMESTAMP
                        """,
                    ),
                )

        # Add completed_activity_id column
        if _column_exists(db, "planned_sessions", "completed_activity_id"):
            logger.info("Column completed_activity_id already exists, skipping")
        else:
            logger.info("Adding completed_activity_id column to planned_sessions table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN completed_activity_id VARCHAR
                        """,
                    ),
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN completed_activity_id VARCHAR
                        """,
                    ),
                )

        db.commit()
        logger.info("Successfully added completion tracking columns to planned_sessions table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_planned_session_completion_fields()
    logger.info("Migration completed successfully")
