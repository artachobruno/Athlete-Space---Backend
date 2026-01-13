"""Migration script to add workout_id column to planned_sessions table.

This migration adds the workout_id column to enforce the mandatory workout invariant:
- If training exists → a workout exists

Usage:
    From project root:
    python scripts/migrate_add_workout_id_to_planned_sessions.py

    Or as a module:
    python -m scripts.migrate_add_workout_id_to_planned_sessions
"""

from __future__ import annotations

import sys
from pathlib import Path

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
from app.db.session import SessionLocal, engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table.

    Args:
        table_name: Name of the table
        column_name: Name of the column

    Returns:
        True if column exists, False otherwise
    """
    with engine.connect() as conn:
        if _is_postgresql():
            result = conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT FROM information_schema.columns
                        WHERE table_schema = 'public'
                        AND table_name = :table_name
                        AND column_name = :column_name
                    )
                    """,
                ),
                {"table_name": table_name, "column_name": column_name},
            )
            return result.scalar() is True

        result = conn.execute(text(f"PRAGMA table_info({table_name})"))
        columns = [row[1] for row in result.fetchall()]
        return column_name in columns


def migrate_add_workout_id_to_planned_sessions() -> None:
    """Add workout_id column to planned_sessions table if it doesn't exist."""
    logger.info("Starting migration: add workout_id column to planned_sessions table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    if _column_exists("planned_sessions", "workout_id"):
        logger.info("workout_id column already exists in planned_sessions table, skipping migration")
        return

    db = SessionLocal()
    try:
        logger.info("Adding workout_id column to planned_sessions table...")

        if _is_postgresql():
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN workout_id VARCHAR
                    """,
                ),
            )
            db.commit()

            # Create index on workout_id
            logger.info("Creating index on workout_id...")
            db.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_planned_sessions_workout_id
                    ON planned_sessions (workout_id)
                    """,
                ),
            )

            # Add foreign key constraint
            logger.info("Adding foreign key constraint...")
            try:
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD CONSTRAINT fk_planned_sessions_workout_id
                        FOREIGN KEY (workout_id) REFERENCES workouts(id)
                        """,
                    ),
                )
                logger.info("Added foreign key constraint")
            except Exception as e:
                logger.warning(f"Could not add foreign key constraint (may already exist): {e}")

            db.commit()
            logger.info("Successfully added workout_id column to planned_sessions table")
        else:
            logger.warning("SQLite detected - workout_id migration requires table recreation")
            logger.info("For SQLite, you may need to recreate the planned_sessions table manually")
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN workout_id VARCHAR
                    """,
                ),
            )
            db.commit()
            logger.info("Added workout_id column (SQLite - foreign key not enforced)")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_workout_id_to_planned_sessions()
    logger.info("Migration completed successfully")
