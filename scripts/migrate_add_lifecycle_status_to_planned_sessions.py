"""Migration: Add lifecycle_status column to planned_sessions.

PHASE 1.1: Introduce lifecycle_status to separate planning intent from execution outcome.

This migration:
- Adds lifecycle_status column (scheduled, moved, cancelled)
- Sets default to 'scheduled' for existing rows
- Does NOT migrate execution outcomes (completed, skipped) - those are derived
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
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import get_session


def migrate_add_lifecycle_status() -> None:
    """Add lifecycle_status column to planned_sessions table."""
    logger.info("Starting migration: add lifecycle_status to planned_sessions")

    with get_session() as session:
        try:
            # Check if column already exists
            check_query = text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'planned_sessions'
                AND column_name = 'lifecycle_status'
            """)
            result = session.execute(check_query).fetchone()

            if result:
                logger.info("Column lifecycle_status already exists, skipping migration")
                return

            # Add lifecycle_status column
            logger.info("Adding lifecycle_status column")
            session.execute(text("""
                ALTER TABLE planned_sessions
                ADD COLUMN lifecycle_status TEXT DEFAULT 'scheduled'
            """))

            # Add CHECK constraint for allowed values
            logger.info("Adding CHECK constraint for lifecycle_status")
            session.execute(text("""
                ALTER TABLE planned_sessions
                ADD CONSTRAINT check_lifecycle_status
                CHECK (lifecycle_status IN ('scheduled', 'moved', 'cancelled'))
            """))

            # Set default for existing rows
            logger.info("Setting lifecycle_status='scheduled' for existing rows")
            session.execute(text("""
                UPDATE planned_sessions
                SET lifecycle_status = 'scheduled'
                WHERE lifecycle_status IS NULL
            """))

            # Make column NOT NULL after setting defaults
            logger.info("Making lifecycle_status NOT NULL")
            session.execute(text("""
                ALTER TABLE planned_sessions
                ALTER COLUMN lifecycle_status SET NOT NULL
            """))

            session.commit()
            logger.info("Migration completed successfully")

        except Exception as e:
            session.rollback()
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_add_lifecycle_status()
