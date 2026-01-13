"""Migration script to set workout_id columns to NOT NULL after backfill.

This migration should be run AFTER backfill_workouts.py completes successfully.
It enforces the mandatory workout invariant at the database level.

Usage:
    From project root:
    python scripts/migrate_set_workout_id_not_null.py

    Or as a module:
    python -m scripts.migrate_set_workout_id_not_null
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


def migrate_set_workout_id_not_null() -> None:
    """Set workout_id columns to NOT NULL after backfill completes.

    WARNING: This migration will fail if there are any NULL values.
    Ensure backfill_workouts.py has been run successfully first.
    """
    logger.info("Starting migration: set workout_id columns to NOT NULL")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    if not _is_postgresql():
        logger.warning("SQLite detected - NOT NULL constraints require table recreation")
        logger.info("For SQLite, you may need to recreate tables manually")
        return

    def _raise_null_values_error(null_planned: int, null_activities: int) -> None:
        """Raise error for NULL workout_id values."""
        raise ValueError(
            f"Cannot set NOT NULL: found {null_planned} planned sessions and "
            f"{null_activities} activities with NULL workout_id"
        )

    db = SessionLocal()
    try:
        # Check for NULL values first (safety check)
        null_planned_result = db.execute(
            text("SELECT COUNT(*) FROM planned_sessions WHERE workout_id IS NULL")
        ).scalar()
        null_activities_result = db.execute(
            text("SELECT COUNT(*) FROM activities WHERE workout_id IS NULL")
        ).scalar()

        # COUNT(*) always returns a number, but handle None for type safety
        null_planned: int
        if null_planned_result is None:
            null_planned = 0
        else:
            null_planned = null_planned_result

        null_activities: int
        if null_activities_result is None:
            null_activities = 0
        else:
            null_activities = null_activities_result

        if null_planned > 0 or null_activities > 0:
            logger.error(
                f"Found NULL values: planned_sessions={null_planned}, activities={null_activities}"
            )
            logger.error("Run backfill_workouts.py first to populate workout_id values")
            _raise_null_values_error(null_planned, null_activities)

        logger.info("No NULL values found, proceeding with NOT NULL constraint")

        # Set planned_sessions.workout_id to NOT NULL
        logger.info("Setting planned_sessions.workout_id to NOT NULL...")
        db.execute(
            text(
                """
                ALTER TABLE planned_sessions
                ALTER COLUMN workout_id SET NOT NULL
                """
            ),
        )
        db.commit()
        logger.info("✓ Set planned_sessions.workout_id to NOT NULL")

        # Set activities.workout_id to NOT NULL
        logger.info("Setting activities.workout_id to NOT NULL...")
        db.execute(
            text(
                """
                ALTER TABLE activities
                ALTER COLUMN workout_id SET NOT NULL
                """
            ),
        )
        db.commit()
        logger.info("✓ Set activities.workout_id to NOT NULL")

        logger.info("Successfully set workout_id columns to NOT NULL")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_set_workout_id_not_null()
    logger.info("Migration completed successfully")
