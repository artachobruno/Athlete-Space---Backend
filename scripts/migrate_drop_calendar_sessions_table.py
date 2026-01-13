"""Migration script to drop calendar_sessions table.

This migration drops the calendar_sessions table as it is fully deprecated.
Calendar data is now derived from planned_sessions, workouts, and activities.

Supports both SQLite and PostgreSQL.
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


def _table_exists(conn, table_name: str) -> bool:
    """Check if table exists (database-agnostic)."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return result.scalar() is True
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def migrate_drop_calendar_sessions_table() -> None:
    """Drop calendar_sessions table if it exists.

    This migration removes the deprecated calendar_sessions table.
    Calendar data is now fully derived from planned_sessions, workouts, and activities.

    Supports both SQLite and PostgreSQL.
    """
    logger.info("Starting calendar_sessions table drop migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.connect() as conn:
        table_exists = _table_exists(conn, "calendar_sessions")

    if not table_exists:
        logger.info("calendar_sessions table does not exist. Nothing to drop.")
        return

    logger.info("calendar_sessions table exists. Dropping table...")

    with engine.begin() as conn:
        # Drop the table
        conn.execute(text("DROP TABLE IF EXISTS calendar_sessions"))
        logger.info("✓ Dropped calendar_sessions table")

    logger.info(f"Migration complete: calendar_sessions table dropped ({db_type})")


if __name__ == "__main__":
    migrate_drop_calendar_sessions_table()
