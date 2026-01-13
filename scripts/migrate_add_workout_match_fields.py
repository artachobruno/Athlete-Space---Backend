"""Migration script to add match tracking fields to workouts table.

This migration adds fields for matching workouts to activities and planned sessions:
- status: Workout status (matched, analyzed, failed)
- activity_id: Foreign key to activities.id
- planned_session_id: Foreign key to planned_sessions.id

Usage:
    From project root:
    python scripts/migrate_add_workout_match_fields.py

    Or as a module:
    python -m scripts.migrate_add_workout_match_fields
"""

from __future__ import annotations

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


def migrate_add_workout_match_fields() -> None:
    """Add match tracking fields to workouts table."""
    logger.info("Starting migration: add match tracking fields to workouts table")

    db = SessionLocal()
    try:
        # Add status column
        if _column_exists(db, "workouts", "status"):
            logger.info("Column status already exists, skipping")
        else:
            logger.info("Adding status column to workouts table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE workouts
                        ADD COLUMN status TEXT NOT NULL DEFAULT 'matched'
                        """,
                    ),
                )
            else:
                # SQLite doesn't support adding NOT NULL columns with defaults directly
                db.execute(
                    text(
                        """
                        ALTER TABLE workouts
                        ADD COLUMN status TEXT
                        """,
                    ),
                )
                # Set default value for existing rows
                db.execute(
                    text(
                        """
                        UPDATE workouts
                        SET status = 'matched'
                        WHERE status IS NULL
                        """,
                    ),
                )
                logger.info("Status column added (nullable in SQLite, application will enforce default)")

        # Add activity_id column
        if _column_exists(db, "workouts", "activity_id"):
            logger.info("Column activity_id already exists, skipping")
        else:
            logger.info("Adding activity_id column to workouts table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE workouts
                        ADD COLUMN activity_id VARCHAR
                        """,
                    ),
                )
                # Add index (foreign key constraint is handled by ORM)
                db.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_workouts_activity_id
                        ON workouts(activity_id)
                        """,
                    ),
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE workouts
                        ADD COLUMN activity_id VARCHAR
                        """,
                    ),
                )
                # SQLite doesn't support adding foreign key constraints via ALTER TABLE
                # The foreign key will be enforced by the ORM

        # Add planned_session_id column
        if _column_exists(db, "workouts", "planned_session_id"):
            logger.info("Column planned_session_id already exists, skipping")
        else:
            logger.info("Adding planned_session_id column to workouts table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE workouts
                        ADD COLUMN planned_session_id VARCHAR
                        """,
                    ),
                )
                # Add index (foreign key constraint is handled by ORM)
                db.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_workouts_planned_session_id
                        ON workouts(planned_session_id)
                        """,
                    ),
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE workouts
                        ADD COLUMN planned_session_id VARCHAR
                        """,
                    ),
                )
                # SQLite doesn't support adding foreign key constraints via ALTER TABLE
                # The foreign key will be enforced by the ORM

        db.commit()
        logger.info("Successfully added match tracking fields to workouts table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_workout_match_fields()
    logger.info("Migration completed successfully")
