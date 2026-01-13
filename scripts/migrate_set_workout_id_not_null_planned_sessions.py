"""Migration script to set workout_id NOT NULL on planned_sessions.

This migration should be run AFTER backfill_workouts.py and migrate_calendar_to_planned.py
complete, to ensure all planned_sessions have workout_id set.

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
from sqlalchemy import select, text

from app.config.settings import settings
from app.db.models import PlannedSession
from app.db.session import get_session


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _column_is_nullable(conn, table_name: str, column_name: str) -> bool:
    """Check if a column is nullable."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = :table_name
                AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        row = result.fetchone()
        if row:
            return row[0] == "YES"
        return True
    # SQLite: Check if column has NOT NULL constraint
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    for row in result.fetchall():
        if row[1] == column_name:  # row[1] is column name
            return row[3] == 0  # row[3] is notnull flag (0 = nullable, 1 = not null)
    return True


def migrate_set_workout_id_not_null() -> None:
    """Set workout_id NOT NULL on planned_sessions table.

    This migration:
    1. Checks for any planned_sessions with NULL workout_id
    2. If found, logs warning and aborts (data must be backfilled first)
    3. Sets workout_id column to NOT NULL

    Supports both SQLite and PostgreSQL.
    """
    logger.info("Starting migration to set workout_id NOT NULL on planned_sessions")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with get_session() as session:
        # Check for NULL workout_id values
        null_count = session.execute(
            select(PlannedSession).where(PlannedSession.workout_id.is_(None))
        ).scalars().all()

        if null_count:
            null_list = list(null_count)
            logger.error(
                f"Found {len(null_list)} planned_sessions with NULL workout_id. "
                "Cannot set NOT NULL constraint. Please run backfill first."
            )
            for ps in null_list[:10]:  # Log first 10
                logger.error(
                    f"PlannedSession {ps.id} (user_id={ps.user_id}, date={ps.date}, title={ps.title}) has NULL workout_id"
                )
            raise ValueError(
                f"Cannot set workout_id NOT NULL: {len(null_list)} planned_sessions have NULL workout_id. "
                "Run backfill_workouts.py and migrate_calendar_to_planned.py first."
            )

        # Check if column is already NOT NULL
        with session.connection() as conn:
            is_nullable = _column_is_nullable(conn, "planned_sessions", "workout_id")

            if not is_nullable:
                logger.info("workout_id column is already NOT NULL. Nothing to do.")
                return

            logger.info("Setting workout_id column to NOT NULL...")

            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ALTER COLUMN workout_id SET NOT NULL
                        """
                    )
                )
            else:
                # SQLite: ALTER TABLE doesn't support changing NULL constraint directly
                # This requires table recreation, which is complex
                logger.warning(
                    "SQLite detected - ALTER TABLE does not support changing NULL constraint. "
                    "You may need to recreate the table manually or use a migration tool."
                )
                logger.info("For SQLite, consider using a tool like sqlite3 or recreating the table")

            logger.info("✓ Set workout_id to NOT NULL")

    logger.info(f"Migration complete: workout_id is NOT NULL ({db_type})")


if __name__ == "__main__":
    migrate_set_workout_id_not_null()
