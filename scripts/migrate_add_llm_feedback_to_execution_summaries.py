"""Migration: Add llm_feedback column to workout_execution_summaries table.

Adds LLM-generated coaching feedback field to execution summaries.
This is cached output, not computed per render.
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


def migrate_add_llm_feedback_to_execution_summaries() -> None:
    """Add llm_feedback column to workout_execution_summaries table."""
    logger.info("Starting migration: add llm_feedback to workout_execution_summaries")

    with get_session() as session:
        try:
            # Check if column already exists
            check_query = text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'workout_execution_summaries'
                AND column_name = 'llm_feedback'
            """)
            result = session.execute(check_query).fetchone()

            if result:
                logger.info("Column llm_feedback already exists, skipping migration")
                return

            # Add column
            logger.info("Adding llm_feedback column")
            session.execute(text("""
                ALTER TABLE workout_execution_summaries
                ADD COLUMN llm_feedback JSONB
            """))

            session.commit()
            logger.info("Migration completed successfully")

        except Exception as e:
            session.rollback()
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_add_llm_feedback_to_execution_summaries()
