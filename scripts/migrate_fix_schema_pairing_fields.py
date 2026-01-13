"""Migration script to fix schema mismatch for pairing fields.

This migration ensures:
- activities.planned_session_id exists with proper foreign key constraint
- planned_sessions.completed_activity_id exists with proper foreign key constraint

Usage:
    From project root:
    python scripts/migrate_fix_schema_pairing_fields.py

    Or as a module:
    python -m scripts.migrate_fix_schema_pairing_fields
"""

from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

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


def _constraint_exists(db, constraint_name: str) -> bool:
    """Check if a constraint exists.

    Args:
        db: Database session
        constraint_name: Name of the constraint

    Returns:
        True if constraint exists, False otherwise
    """
    if _is_postgresql():
        result = db.execute(
            text(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE constraint_name = :constraint_name
                """,
            ),
            {"constraint_name": constraint_name},
        ).fetchone()
        return result is not None
    return False


def migrate_fix_schema_pairing_fields() -> None:
    """Fix schema mismatch by adding missing pairing fields."""
    logger.info("Starting migration: fix schema pairing fields")

    db = SessionLocal()
    try:
        # Step 1: Add planned_session_id to activities table
        if _column_exists(db, "activities", "planned_session_id"):
            logger.info("Column planned_session_id already exists in activities table")

            # Check if constraint exists with correct name
            if _is_postgresql():
                if not _constraint_exists(db, "fk_activities_planned_session"):
                    logger.info("Adding foreign key constraint fk_activities_planned_session")
                    # Drop existing constraint if it has different name
                    with suppress(Exception):
                        db.execute(
                            text(
                                """
                                ALTER TABLE activities
                                DROP CONSTRAINT IF EXISTS fk_activities_planned_session_id
                                """,
                            ),
                        )

                    db.execute(
                        text(
                            """
                            ALTER TABLE activities
                            ADD CONSTRAINT fk_activities_planned_session
                            FOREIGN KEY (planned_session_id)
                            REFERENCES planned_sessions(id)
                            ON DELETE SET NULL
                            """,
                        ),
                    )
                else:
                    logger.info("Foreign key constraint fk_activities_planned_session already exists")
        else:
            logger.info("Adding planned_session_id column to activities table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN planned_session_id character varying
                        """,
                    ),
                )
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD CONSTRAINT fk_activities_planned_session
                        FOREIGN KEY (planned_session_id)
                        REFERENCES planned_sessions(id)
                        ON DELETE SET NULL
                        """,
                    ),
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE activities
                        ADD COLUMN planned_session_id VARCHAR
                        """,
                    ),
                )
                logger.info("Column added (SQLite foreign key enforcement at application level)")

        # Step 2: Add completed_activity_id to planned_sessions table if missing
        if _column_exists(db, "planned_sessions", "completed_activity_id"):
            logger.info("Column completed_activity_id already exists in planned_sessions table")

            # Check if constraint exists
            if _is_postgresql():
                if not _constraint_exists(db, "fk_planned_completed_activity"):
                    logger.info("Adding foreign key constraint fk_planned_completed_activity")
                    db.execute(
                        text(
                            """
                            ALTER TABLE planned_sessions
                            ADD CONSTRAINT fk_planned_completed_activity
                            FOREIGN KEY (completed_activity_id)
                            REFERENCES activities(id)
                            ON DELETE SET NULL
                            """,
                        ),
                    )
                else:
                    logger.info("Foreign key constraint fk_planned_completed_activity already exists")
        else:
            logger.info("Adding completed_activity_id column to planned_sessions table")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN completed_activity_id character varying
                        """,
                    ),
                )
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD CONSTRAINT fk_planned_completed_activity
                        FOREIGN KEY (completed_activity_id)
                        REFERENCES activities(id)
                        ON DELETE SET NULL
                        """,
                    ),
                )
            else:
                db.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN completed_activity_id VARCHAR
                        """,
                    ),
                )
                logger.info("Column added (SQLite foreign key enforcement at application level)")

        db.commit()
        logger.info("Successfully fixed schema pairing fields")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_fix_schema_pairing_fields()
    logger.info("Migration completed successfully")
