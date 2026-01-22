"""Migration script to add execution_notes column to planned_sessions table.

This migration adds the execution_notes column (VARCHAR(120), nullable)
to the planned_sessions table for storing execution guidance notes.
"""

import sys
from pathlib import Path

# Add project root to Python path
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

project_root_str = str(project_root)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)

from loguru import logger
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.session import SessionLocal


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _column_exists(session: Session, table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    inspector = inspect(session.bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def migrate_add_execution_notes_to_planned_sessions() -> None:
    """Add execution_notes column to planned_sessions table."""
    logger.info("Starting migration: add execution_notes column to planned_sessions table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    db = SessionLocal()
    try:
        if _column_exists(db, "planned_sessions", "execution_notes"):
            logger.info("Column execution_notes already exists, skipping migration")
            return

        logger.info("Adding execution_notes column to planned_sessions table")
        if _is_postgresql():
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN execution_notes VARCHAR(120)
                    """
                ),
            )
        else:
            # SQLite
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN execution_notes VARCHAR(120)
                    """
                ),
            )

        db.commit()
        logger.info("Successfully added execution_notes column to planned_sessions table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_execution_notes_to_planned_sessions()
