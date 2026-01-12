"""Migration script to add unique index for planned_sessions idempotency.

This migration creates a unique constraint on (plan_id, user_id, athlete_id, date, title)
to ensure idempotent retries. When plan_id is NULL, we use a partial index in PostgreSQL
or handle it differently in SQLite.
"""

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
from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _index_exists(conn, index_name: str) -> bool:
    """Check if index exists."""
    if _is_postgresql():
        result = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname = :index_name"),
            {"index_name": index_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:index_name"),
        {"index_name": index_name},
    )
    return result.fetchone() is not None


def migrate_add_planned_sessions_unique_index() -> None:
    """Add unique index for planned_sessions idempotency."""
    logger.info("Starting planned_sessions unique index migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    index_name = "uniq_plan_session"

    with engine.begin() as conn:
        try:
            # Check if index already exists
            if _index_exists(conn, index_name):
                logger.info(f"Index {index_name} already exists, skipping migration")
                return

            logger.info(f"Creating unique index {index_name}...")

            if _is_postgresql():
                # PostgreSQL: Create unique index on (plan_id, user_id, athlete_id, date, title)
                # Use partial index to handle NULL plan_id (only index rows where plan_id IS NOT NULL)
                # For rows with NULL plan_id, we rely on the existing duplicate check logic
                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX uniq_plan_session
                        ON planned_sessions(plan_id, user_id, athlete_id, date, title)
                        WHERE plan_id IS NOT NULL
                        """
                    )
                )
                logger.info("Created unique index for planned_sessions (PostgreSQL, partial index)")
            else:
                # SQLite: Create unique index (SQLite handles NULLs differently)
                # Note: SQLite allows multiple NULLs in unique indexes, so this works
                conn.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX uniq_plan_session
                        ON planned_sessions(plan_id, user_id, athlete_id, date, title)
                        """
                    )
                )
                logger.info("Created unique index for planned_sessions (SQLite)")

            logger.info("Migration complete: Added unique index for planned_sessions idempotency")

        except Exception as e:
            logger.error(f"Error during planned_sessions unique index migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_add_planned_sessions_unique_index()
