"""Migration script to create workout execution and compliance tables.

This migration creates:
- workout_executions: Links workouts to executed activities
  - id: UUID primary key (VARCHAR)
  - workout_id: Foreign key to workouts.id (VARCHAR, indexed)
  - activity_id: Foreign key to activities.id (VARCHAR, indexed)
  - attached_at: Attachment timestamp (TIMESTAMP)

- step_compliance: Compliance metrics per workout step
  - id: UUID primary key (VARCHAR)
  - workout_step_id: Foreign key to workout_steps.id (VARCHAR, indexed)
  - duration_seconds: Total duration (INTEGER)
  - time_in_range_seconds: Time in target range (INTEGER)
  - overshoot_seconds: Time above target (INTEGER)
  - undershoot_seconds: Time below target (INTEGER)
  - pause_seconds: Time paused (INTEGER)
  - compliance_pct: Compliance percentage (FLOAT)

- workout_compliance_summary: Overall workout compliance
  - workout_id: UUID primary key (VARCHAR, foreign key to workouts.id)
  - overall_compliance_pct: Weighted average compliance (FLOAT)
  - total_pause_seconds: Total pause time (INTEGER)
  - completed: Completion status (BOOLEAN)

Constraints:
- Foreign keys: workout_executions -> workouts, activities
- Foreign keys: step_compliance -> workout_steps
- Foreign keys: workout_compliance_summary -> workouts
- Indexes on foreign keys for fast queries
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


def migrate_create_workout_execution_tables() -> None:
    """Create workout execution and compliance tables if they don't exist."""
    logger.info("Starting workout execution tables migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Create workout_executions table
            if _table_exists(conn, "workout_executions"):
                logger.info("workout_executions table already exists, skipping")
            else:
                logger.info("Creating workout_executions table...")

                if _is_postgresql():
                    conn.execute(
                        text(
                            """
                            CREATE TABLE workout_executions (
                                id VARCHAR PRIMARY KEY,
                                workout_id VARCHAR NOT NULL,
                                activity_id VARCHAR NOT NULL,
                                attached_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                                CONSTRAINT fk_workout_executions_workout_id
                                    FOREIGN KEY (workout_id) REFERENCES workouts(id),
                                CONSTRAINT fk_workout_executions_activity_id
                                    FOREIGN KEY (activity_id) REFERENCES activities(id)
                            )
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_workout_executions_workout_id
                            ON workout_executions (workout_id)
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_workout_executions_activity_id
                            ON workout_executions (activity_id)
                            """
                        )
                    )
                else:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE workout_executions (
                                id TEXT PRIMARY KEY,
                                workout_id TEXT NOT NULL,
                                activity_id TEXT NOT NULL,
                                attached_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                                FOREIGN KEY (workout_id) REFERENCES workouts(id),
                                FOREIGN KEY (activity_id) REFERENCES activities(id)
                            )
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_workout_executions_workout_id
                            ON workout_executions (workout_id)
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_workout_executions_activity_id
                            ON workout_executions (activity_id)
                            """
                        )
                    )

                logger.info("workout_executions table created successfully")

            # Create step_compliance table
            if _table_exists(conn, "step_compliance"):
                logger.info("step_compliance table already exists, skipping")
            else:
                logger.info("Creating step_compliance table...")

                if _is_postgresql():
                    conn.execute(
                        text(
                            """
                            CREATE TABLE step_compliance (
                                id VARCHAR PRIMARY KEY,
                                workout_step_id VARCHAR NOT NULL,
                                duration_seconds INTEGER NOT NULL,
                                time_in_range_seconds INTEGER NOT NULL,
                                overshoot_seconds INTEGER NOT NULL,
                                undershoot_seconds INTEGER NOT NULL,
                                pause_seconds INTEGER NOT NULL,
                                compliance_pct FLOAT NOT NULL,
                                CONSTRAINT fk_step_compliance_workout_step_id
                                    FOREIGN KEY (workout_step_id) REFERENCES workout_steps(id)
                            )
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_step_compliance_workout_step_id
                            ON step_compliance (workout_step_id)
                            """
                        )
                    )
                else:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE step_compliance (
                                id TEXT PRIMARY KEY,
                                workout_step_id TEXT NOT NULL,
                                duration_seconds INTEGER NOT NULL,
                                time_in_range_seconds INTEGER NOT NULL,
                                overshoot_seconds INTEGER NOT NULL,
                                undershoot_seconds INTEGER NOT NULL,
                                pause_seconds INTEGER NOT NULL,
                                compliance_pct REAL NOT NULL,
                                FOREIGN KEY (workout_step_id) REFERENCES workout_steps(id)
                            )
                            """
                        )
                    )

                    conn.execute(
                        text(
                            """
                            CREATE INDEX idx_step_compliance_workout_step_id
                            ON step_compliance (workout_step_id)
                            """
                        )
                    )

                logger.info("step_compliance table created successfully")

            # Create workout_compliance_summary table
            if _table_exists(conn, "workout_compliance_summary"):
                logger.info("workout_compliance_summary table already exists, skipping")
            else:
                logger.info("Creating workout_compliance_summary table...")

                if _is_postgresql():
                    conn.execute(
                        text(
                            """
                            CREATE TABLE workout_compliance_summary (
                                workout_id VARCHAR PRIMARY KEY,
                                overall_compliance_pct FLOAT NOT NULL,
                                total_pause_seconds INTEGER NOT NULL,
                                completed BOOLEAN NOT NULL,
                                CONSTRAINT fk_workout_compliance_summary_workout_id
                                    FOREIGN KEY (workout_id) REFERENCES workouts(id)
                            )
                            """
                        )
                    )
                else:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE workout_compliance_summary (
                                workout_id TEXT PRIMARY KEY,
                                overall_compliance_pct REAL NOT NULL,
                                total_pause_seconds INTEGER NOT NULL,
                                completed INTEGER NOT NULL,
                                FOREIGN KEY (workout_id) REFERENCES workouts(id)
                            )
                            """
                        )
                    )

                logger.info("workout_compliance_summary table created successfully")

            logger.info("Workout execution tables migration completed successfully")

        except Exception as e:
            logger.error(f"Error during workout execution tables migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_create_workout_execution_tables()
