"""Migration script to add revision_id column to planned_sessions table.

This migration adds:
- revision_id: Optional reference to plan_revisions.id
  This links modified sessions to their revision records.
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import text

from app.config.settings import settings
from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = :table_name
                AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(
        text("PRAGMA table_info(:table_name)"),
        {"table_name": table_name},
    )
    columns = [row[1] for row in result.fetchall()]
    return column_name in columns


def migrate_add_revision_id_to_planned_sessions() -> None:
    """Add revision_id column to planned_sessions table if it doesn't exist."""
    logger.info("Starting revision_id column migration for planned_sessions")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if column already exists
            if _column_exists(conn, "planned_sessions", "revision_id"):
                logger.info("revision_id column already exists in planned_sessions, skipping migration")
                return

            logger.info("Adding revision_id column to planned_sessions table...")

            if _is_postgresql():
                conn.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN revision_id VARCHAR
                        """
                    )
                )

                # Create index
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_planned_sessions_revision_id
                        ON planned_sessions (revision_id)
                        """
                    )
                )
            else:
                conn.execute(
                    text(
                        """
                        ALTER TABLE planned_sessions
                        ADD COLUMN revision_id TEXT
                        """
                    )
                )

                # Create index
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_planned_sessions_revision_id
                        ON planned_sessions (revision_id)
                        """
                    )
                )

            logger.info("revision_id column added successfully to planned_sessions")

        except Exception as e:
            logger.error(f"Error during revision_id column migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_add_revision_id_to_planned_sessions()
