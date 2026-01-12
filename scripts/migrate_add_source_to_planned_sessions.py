"""Add source column to planned_sessions table.

This migration adds the source column to track which system generated the planned session.

Usage:
    From project root:
    python scripts/migrate_add_source_to_planned_sessions.py

    Or as a module:
    python -m scripts.migrate_add_source_to_planned_sessions
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path (must be absolute for Render/production)
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

# Verify project root contains app directory or pyproject.toml
if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    # If parent doesn't have app/ or pyproject.toml, try current working directory
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        # Last resort: try going up one more level (for cases where script is in src/scripts/)
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

# Ensure project root is in path
project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

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


def migrate_add_source_to_planned_sessions() -> None:
    """Add source column to planned_sessions table."""
    logger.info("Starting migration: add source column to planned_sessions table")

    db = SessionLocal()
    try:
        if _column_exists(db, "planned_sessions", "source"):
            logger.info("Column source already exists, skipping migration")
            return

        logger.info("Adding source column to planned_sessions table")
        if _is_postgresql():
            # Add column with default value
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN source VARCHAR NOT NULL DEFAULT 'planner_v2'
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
                    ADD COLUMN source VARCHAR
                    """,
                ),
            )
            # Set default value for existing rows
            db.execute(
                text(
                    """
                    UPDATE planned_sessions
                    SET source = 'planner_v2'
                    WHERE source IS NULL
                    """,
                ),
            )
            # SQLite doesn't support ALTER COLUMN, so we'd need to recreate the table
            # For now, we'll leave it nullable but with a default in the application
            logger.info("Source column added (nullable in SQLite, application will enforce default)")

        db.commit()
        logger.info("Successfully added source column to planned_sessions table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_source_to_planned_sessions()
    logger.info("Migration completed successfully")
