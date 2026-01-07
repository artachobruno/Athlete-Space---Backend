# ⚠️ LEGACY: One-time migration script - not included in run_migrations.py
# Keep for historical reference and potential database recovery scenarios
"""Migration script to add status column to planned_sessions table.

This migration adds a status column to track session status (planned, completed, skipped, cancelled).

Usage:
    From project root:
    python scripts/migrate_add_planned_session_status.py

    Or as a module:
    python -m scripts.migrate_add_planned_session_status
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


def migrate_add_planned_session_status() -> None:
    """Add status column to planned_sessions table."""
    logger.info("Starting migration: add status column to planned_sessions table")

    db = SessionLocal()
    try:
        if _column_exists(db, "planned_sessions", "status"):
            logger.info("Column status already exists, skipping migration")
            return

        logger.info("Adding status column to planned_sessions table")
        if _is_postgresql():
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'planned'
                    """,
                ),
            )
        else:
            # SQLite doesn't support adding NOT NULL columns with defaults directly
            # We need to add it as nullable first, set default values, then make it NOT NULL
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN status VARCHAR(20)
                    """,
                ),
            )
            # Set default value for existing rows
            db.execute(
                text(
                    """
                    UPDATE planned_sessions
                    SET status = 'planned'
                    WHERE status IS NULL
                    """,
                ),
            )
            # SQLite doesn't support ALTER COLUMN, so we'd need to recreate the table
            # For now, we'll leave it nullable but with a default in the application
            logger.info("Status column added (nullable in SQLite, application will enforce default)")

        db.commit()
        logger.info("Successfully added status column to planned_sessions table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_planned_session_status()
    logger.info("Migration completed successfully")
