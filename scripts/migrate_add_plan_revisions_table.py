"""Migration script to create plan_revisions table.

This migration creates:
- plan_revisions: Append-only audit table for plan modifications
  - id: UUID primary key
  - user_id: User ID who made the modification
  - athlete_id: Athlete ID whose plan was modified
  - revision_type: Type of revision (modify_day, modify_week, modify_season, modify_race)
  - status: Status of revision (applied, blocked)
  - reason: Optional reason for modification
  - blocked_reason: Optional reason if blocked
  - affected_start: Start date of affected range (nullable)
  - affected_end: End date of affected range (nullable)
  - deltas: JSON field storing before/after snapshots and changes
  - created_at: Timestamp when revision was created

Constraints:
- Append-only (no updates or deletes)
- Indexed on (athlete_id, created_at) for efficient querying
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


def _table_exists(conn, table_name: str) -> bool:
    """Check if table exists."""
    if _is_postgresql():
        result = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def migrate_add_plan_revisions_table() -> None:
    """Create plan_revisions table if it doesn't exist."""
    logger.info("Starting plan_revisions table migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if table already exists
            if _table_exists(conn, "plan_revisions"):
                logger.info("plan_revisions table already exists, skipping migration")
                return

            logger.info("Creating plan_revisions table...")

            if _is_postgresql():
                # PostgreSQL: Use VARCHAR for id, JSONB for deltas, DATE for dates
                conn.execute(
                    text(
                        """
                        CREATE TABLE plan_revisions (
                            id VARCHAR PRIMARY KEY,
                            user_id VARCHAR NOT NULL,
                            athlete_id INTEGER NOT NULL,
                            revision_type VARCHAR NOT NULL,
                            status VARCHAR NOT NULL,
                            reason TEXT,
                            blocked_reason TEXT,
                            affected_start DATE,
                            affected_end DATE,
                            deltas JSONB,
                            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                )

                # Create indexes
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_plan_revisions_athlete_created
                        ON plan_revisions (athlete_id, created_at DESC)
                        """
                    )
                )

                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_plan_revisions_user_id
                        ON plan_revisions (user_id)
                        """
                    )
                )

                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_plan_revisions_created_at
                        ON plan_revisions (created_at DESC)
                        """
                    )
                )
            else:
                # SQLite: Use TEXT for id, TEXT for JSON, DATE for dates
                conn.execute(
                    text(
                        """
                        CREATE TABLE plan_revisions (
                            id TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL,
                            athlete_id INTEGER NOT NULL,
                            revision_type TEXT NOT NULL,
                            status TEXT NOT NULL,
                            reason TEXT,
                            blocked_reason TEXT,
                            affected_start DATE,
                            affected_end DATE,
                            deltas TEXT,
                            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )

                # Create indexes
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_plan_revisions_athlete_created
                        ON plan_revisions (athlete_id, created_at DESC)
                        """
                    )
                )

                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_plan_revisions_user_id
                        ON plan_revisions (user_id)
                        """
                    )
                )

                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_plan_revisions_created_at
                        ON plan_revisions (created_at DESC)
                        """
                    )
                )

            logger.info("plan_revisions table created successfully")

        except Exception as e:
            logger.error(f"Error during plan_revisions table migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_add_plan_revisions_table()
