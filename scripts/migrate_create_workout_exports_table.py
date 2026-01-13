"""Migration script to create workout_exports table.

This migration creates:
- workout_exports: Export tracking table
  - id: UUID primary key (VARCHAR)
  - workout_id: Foreign key to workouts.id (VARCHAR, indexed)
  - export_type: Export format type (VARCHAR)
  - status: Export status (VARCHAR)
  - file_path: Path to generated file (VARCHAR, nullable)
  - error_message: Error message if failed (TEXT, nullable)
  - created_at: Creation timestamp (TIMESTAMP)

Constraints:
- Foreign key: workout_exports.workout_id -> workouts.id
- Index on workout_exports.workout_id for fast queries
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


def migrate_create_workout_exports_table() -> None:
    """Create workout_exports table if it doesn't exist."""
    logger.info("Starting workout_exports table migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if table already exists
            if _table_exists(conn, "workout_exports"):
                logger.info("workout_exports table already exists, skipping migration")
                return

            logger.info("Creating workout_exports table...")

            if _is_postgresql():
                # PostgreSQL: Use VARCHAR for UUIDs, TIMESTAMP WITH TIME ZONE
                conn.execute(
                    text(
                        """
                        CREATE TABLE workout_exports (
                            id VARCHAR PRIMARY KEY,
                            workout_id VARCHAR NOT NULL,
                            export_type VARCHAR NOT NULL,
                            status VARCHAR NOT NULL,
                            file_path VARCHAR,
                            error_message TEXT,
                            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                            CONSTRAINT fk_workout_exports_workout_id
                                FOREIGN KEY (workout_id) REFERENCES workouts(id)
                        )
                        """
                    )
                )

                # Create indexes
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_workout_exports_workout_id
                        ON workout_exports (workout_id)
                        """
                    )
                )
            else:
                # SQLite: Use TEXT for UUIDs, DATETIME
                conn.execute(
                    text(
                        """
                        CREATE TABLE workout_exports (
                            id TEXT PRIMARY KEY,
                            workout_id TEXT NOT NULL,
                            export_type TEXT NOT NULL,
                            status TEXT NOT NULL,
                            file_path TEXT,
                            error_message TEXT,
                            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (workout_id) REFERENCES workouts(id)
                        )
                        """
                    )
                )

                # Create indexes
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_workout_exports_workout_id
                        ON workout_exports (workout_id)
                        """
                    )
                )

            logger.info("Workout exports table created successfully")

        except Exception as e:
            logger.error(f"Error during workout_exports table migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_create_workout_exports_table()
