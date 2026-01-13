"""Migration script to create workouts and workout_steps tables.

This migration creates:
- workouts: Canonical workout table
  - id: UUID primary key (VARCHAR)
  - user_id: User ID (VARCHAR, indexed)
  - sport: Sport type (VARCHAR)
  - source: Source system (VARCHAR)
  - source_ref: Optional source reference (VARCHAR, nullable)
  - total_duration_seconds: Total duration (INTEGER, nullable)
  - total_distance_meters: Total distance (INTEGER, nullable)
  - created_at: Creation timestamp (TIMESTAMP)

- workout_steps: Individual steps within workouts
  - id: UUID primary key (VARCHAR)
  - workout_id: Foreign key to workouts.id (VARCHAR, indexed)
  - order: Step order (INTEGER)
  - type: Step type (VARCHAR)
  - duration_seconds: Step duration (INTEGER, nullable)
  - distance_meters: Step distance (INTEGER, nullable)
  - target_metric: Target metric type (VARCHAR, nullable)
  - target_min: Minimum target value (FLOAT, nullable)
  - target_max: Maximum target value (FLOAT, nullable)
  - target_value: Single target value (FLOAT, nullable)
  - intensity_zone: Intensity zone (VARCHAR, nullable)
  - instructions: Step instructions (TEXT, nullable)
  - purpose: Step purpose (TEXT, nullable)
  - inferred: Whether step was inferred (BOOLEAN, default: false)

Constraints:
- Foreign key: workout_steps.workout_id -> workouts.id
- Index on workouts.user_id for fast user queries
- Index on workout_steps.workout_id for fast step queries
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


def migrate_create_workouts_tables() -> None:
    """Create workouts and workout_steps tables if they don't exist."""
    logger.info("Starting workouts tables migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        try:
            # Check if tables already exist
            if _table_exists(conn, "workouts"):
                logger.info("workouts table already exists, checking for missing columns...")
                # Check if status column exists, if not add it
                if _is_postgresql():
                    result = conn.execute(
                        text(
                            """
                            SELECT column_name 
                            FROM information_schema.columns 
                            WHERE table_name = 'workouts' AND column_name = 'status'
                            """
                        )
                    )
                    if result.fetchone() is None:
                        logger.info("Adding missing status column to workouts table...")
                        conn.execute(text("ALTER TABLE workouts ADD COLUMN status VARCHAR NOT NULL DEFAULT 'matched'"))
                        logger.info("✓ Added status column")
                    
                    # Check for activity_id and planned_session_id columns
                    for col_name in ['activity_id', 'planned_session_id']:
                        result = conn.execute(
                            text(
                                f"""
                                SELECT column_name 
                                FROM information_schema.columns 
                                WHERE table_name = 'workouts' AND column_name = '{col_name}'
                                """
                            )
                        )
                        if result.fetchone() is None:
                            logger.info(f"Adding missing {col_name} column to workouts table...")
                            conn.execute(text(f"ALTER TABLE workouts ADD COLUMN {col_name} VARCHAR"))
                            logger.info(f"✓ Added {col_name} column")
                    
                    # Create indexes if they don't exist
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_workouts_activity_id ON workouts(activity_id)"))
                    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_workouts_planned_session_id ON workouts(planned_session_id)"))
                    logger.info("✓ Ensured indexes exist")
                else:
                    logger.warning("SQLite detected - column addition requires table recreation. Skipping.")
                logger.info("workouts table schema updated")
                return

            logger.info("Creating workouts table...")

            if _is_postgresql():
                # PostgreSQL: Use VARCHAR for UUIDs, TIMESTAMP WITH TIME ZONE
                conn.execute(
                    text(
                        """
                        CREATE TABLE workouts (
                            id VARCHAR PRIMARY KEY,
                            user_id VARCHAR NOT NULL,
                            sport VARCHAR NOT NULL,
                            source VARCHAR NOT NULL,
                            source_ref VARCHAR,
                            total_duration_seconds INTEGER,
                            total_distance_meters INTEGER,
                            status VARCHAR NOT NULL DEFAULT 'matched',
                            activity_id VARCHAR,
                            planned_session_id VARCHAR,
                            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                )

                # Create indexes
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_workouts_user_id
                        ON workouts (user_id)
                        """
                    )
                )

                logger.info("Creating workout_steps table...")

                conn.execute(
                    text(
                        """
                        CREATE TABLE workout_steps (
                            id VARCHAR PRIMARY KEY,
                            workout_id VARCHAR NOT NULL,
                            "order" INTEGER NOT NULL,
                            type VARCHAR NOT NULL,
                            duration_seconds INTEGER,
                            distance_meters INTEGER,
                            target_metric VARCHAR,
                            target_min FLOAT,
                            target_max FLOAT,
                            target_value FLOAT,
                            intensity_zone VARCHAR,
                            instructions TEXT,
                            purpose TEXT,
                            inferred BOOLEAN NOT NULL DEFAULT FALSE,
                            CONSTRAINT fk_workout_steps_workout_id
                                FOREIGN KEY (workout_id) REFERENCES workouts(id)
                        )
                        """
                    )
                )

                # Create indexes
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_workout_steps_workout_id
                        ON workout_steps (workout_id)
                        """
                    )
                )
            else:
                # SQLite: Use TEXT for UUIDs, DATETIME
                conn.execute(
                    text(
                        """
                        CREATE TABLE workouts (
                            id TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL,
                            sport TEXT NOT NULL,
                            source TEXT NOT NULL,
                            source_ref TEXT,
                            total_duration_seconds INTEGER,
                            total_distance_meters INTEGER,
                            status TEXT NOT NULL DEFAULT 'matched',
                            activity_id TEXT,
                            planned_session_id TEXT,
                            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )

                # Create indexes
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_workouts_user_id
                        ON workouts (user_id)
                        """
                    )
                )

                logger.info("Creating workout_steps table...")

                conn.execute(
                    text(
                        """
                        CREATE TABLE workout_steps (
                            id TEXT PRIMARY KEY,
                            workout_id TEXT NOT NULL,
                            "order" INTEGER NOT NULL,
                            type TEXT NOT NULL,
                            duration_seconds INTEGER,
                            distance_meters INTEGER,
                            target_metric TEXT,
                            target_min REAL,
                            target_max REAL,
                            target_value REAL,
                            intensity_zone TEXT,
                            instructions TEXT,
                            purpose TEXT,
                            inferred INTEGER NOT NULL DEFAULT 0,
                            FOREIGN KEY (workout_id) REFERENCES workouts(id)
                        )
                        """
                    )
                )

                # Create indexes
                conn.execute(
                    text(
                        """
                        CREATE INDEX idx_workout_steps_workout_id
                        ON workout_steps (workout_id)
                        """
                    )
                )

            logger.info("Workouts tables created successfully")

        except Exception as e:
            logger.error(f"Error during workouts tables migration: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    migrate_create_workouts_tables()
