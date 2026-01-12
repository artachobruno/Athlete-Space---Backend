"""Migration script to add session_order column to planned_sessions table.

This migration adds the session_order column which is used for ordering
sessions within a day (0-based, for idempotency).

Usage:
    From project root:
    python scripts/migrate_add_session_order_to_planned_sessions.py

    Or as a module:
    python -m scripts.migrate_add_session_order_to_planned_sessions
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


def migrate_add_session_order_to_planned_sessions() -> None:
    """Add session_order column to planned_sessions table."""
    logger.info("Starting migration: add session_order column to planned_sessions table")

    db = SessionLocal()
    try:
        if _column_exists(db, "planned_sessions", "session_order"):
            logger.info("Column session_order already exists, skipping migration")
            return

        logger.info("Adding session_order column to planned_sessions table")
        if _is_postgresql():
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN session_order INTEGER
                    """,
                ),
            )
        else:
            # SQLite
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN session_order INTEGER
                    """,
                ),
            )

        db.commit()
        logger.info("Successfully added session_order column to planned_sessions table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_session_order_to_planned_sessions()
    logger.info("Migration completed successfully")
