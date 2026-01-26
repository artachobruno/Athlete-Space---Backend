"""Migration: Create workout_execution_summaries table.

PHASE 5.1: Introduce execution summary artifact.

This table stores computed execution summaries to avoid repeated recomputation.
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


def migrate_create_workout_execution_summaries() -> None:
    """Create workout_execution_summaries table."""
    logger.info("Starting migration: create workout_execution_summaries table")

    with get_session() as session:
        try:
            # Check if table already exists
            check_query = text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name = 'workout_execution_summaries'
            """)
            result = session.execute(check_query).fetchone()

            if result:
                logger.info("Table workout_execution_summaries already exists, skipping migration")
                return

            # Create table
            logger.info("Creating workout_execution_summaries table")
            session.execute(text("""
                CREATE TABLE workout_execution_summaries (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    activity_id UUID NOT NULL UNIQUE REFERENCES activities(id) ON DELETE CASCADE,
                    planned_session_id UUID REFERENCES planned_sessions(id) ON DELETE SET NULL,
                    user_id TEXT NOT NULL,
                    compliance_score FLOAT,
                    step_comparison JSONB,
                    narrative TEXT,
                    computed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            """))

            # Create indexes
            logger.info("Creating indexes")
            session.execute(text("""
                CREATE INDEX idx_workout_execution_summaries_activity
                ON workout_execution_summaries(activity_id)
            """))

            session.execute(text("""
                CREATE INDEX idx_workout_execution_summaries_planned_session
                ON workout_execution_summaries(planned_session_id)
            """))

            session.execute(text("""
                CREATE INDEX idx_workout_execution_summaries_user
                ON workout_execution_summaries(user_id)
            """))

            session.commit()
            logger.info("Migration completed successfully")

        except Exception as e:
            session.rollback()
            logger.error(f"Migration failed: {e}")
            raise


if __name__ == "__main__":
    migrate_create_workout_execution_summaries()
