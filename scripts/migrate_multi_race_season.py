"""Migration script for multi-race season support.

This migration adds:
- race_plans table with priority field (A/B/C enum, default A)
- active_race_id column to conversation_progress table
- race_priority enum type (PostgreSQL only)
- Indexes and constraints

Usage:
    From project root:
    python scripts/migrate_multi_race_season.py

    Or as a module:
    python -m scripts.migrate_multi_race_season
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path
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
from app.db.session import SessionLocal


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _table_exists(db, table_name: str) -> bool:
    """Check if a table exists.

    Args:
        db: Database session
        table_name: Name of the table

    Returns:
        True if table exists, False otherwise
    """
    if _is_postgresql():
        result = db.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public' AND tablename = :table_name
                """
            ),
            {"table_name": table_name},
        ).fetchone()
        return result is not None
    result = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    ).fetchone()
    return result is not None


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
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        ).fetchone()
        return result is not None
    result = db.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return any(col[1] == column_name for col in result)


def _enum_type_exists(db, type_name: str) -> bool:
    """Check if an enum type exists (PostgreSQL only).

    Args:
        db: Database session
        type_name: Name of the enum type

    Returns:
        True if enum exists, False otherwise
    """
    if not _is_postgresql():
        return False
    result = db.execute(
        text(
            """
            SELECT typname
            FROM pg_type
            WHERE typname = :type_name
            """
        ),
        {"type_name": type_name},
    ).fetchone()
    return result is not None


def _index_exists(db, index_name: str) -> bool:
    """Check if an index exists.

    Args:
        db: Database session
        index_name: Name of the index

    Returns:
        True if index exists, False otherwise
    """
    if _is_postgresql():
        result = db.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE indexname = :index_name
                """
            ),
            {"index_name": index_name},
        ).fetchone()
        return result is not None
    result = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:index_name"),
        {"index_name": index_name},
    ).fetchone()
    return result is not None


def _constraint_exists(db, constraint_name: str) -> bool:
    """Check if a constraint exists (PostgreSQL only).

    Args:
        db: Database session
        constraint_name: Name of the constraint

    Returns:
        True if constraint exists, False otherwise
    """
    if not _is_postgresql():
        return False
    result = db.execute(
        text(
            """
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE constraint_name = :constraint_name
            """
        ),
        {"constraint_name": constraint_name},
    ).fetchone()
    return result is not None


def migrate_multi_race_season() -> None:
    """Add multi-race season support: race_plans table and active_race_id column."""
    logger.info("Starting migration: multi-race season support")

    db = SessionLocal()
    try:
        # Step 1: Create race_priority enum type (PostgreSQL only)
        if _is_postgresql():
            if not _enum_type_exists(db, "race_priority"):
                logger.info("Creating race_priority enum type...")
                db.execute(text("CREATE TYPE race_priority AS ENUM ('A', 'B', 'C')"))
                db.commit()
                logger.info("✓ Created race_priority enum type")
            else:
                logger.info("race_priority enum type already exists, skipping")

        # Step 2: Create race_plans table
        if not _table_exists(db, "race_plans"):
            logger.info("Creating race_plans table...")
            if _is_postgresql():
                db.execute(
                    text(
                        """
                        CREATE TABLE race_plans (
                            id VARCHAR PRIMARY KEY,
                            user_id VARCHAR NOT NULL,
                            athlete_id INTEGER NOT NULL,
                            race_date TIMESTAMP NOT NULL,
                            race_distance VARCHAR NOT NULL,
                            race_name VARCHAR,
                            target_time VARCHAR,
                            priority VARCHAR NOT NULL DEFAULT 'A',
                            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
                # Create indexes
                db.execute(text("CREATE INDEX idx_race_plans_user_id ON race_plans(user_id)"))
                db.execute(text("CREATE INDEX idx_race_plans_athlete_id ON race_plans(athlete_id)"))
                db.execute(text("CREATE INDEX idx_race_plans_race_date ON race_plans(race_date)"))
                db.execute(text("CREATE INDEX idx_race_plan_athlete_priority ON race_plans(athlete_id, priority)"))
                # Create unique constraint
                if not _constraint_exists(db, "uq_race_plan_athlete_date_distance"):
                    db.execute(
                        text(
                            """
                            ALTER TABLE race_plans
                            ADD CONSTRAINT uq_race_plan_athlete_date_distance
                            UNIQUE (athlete_id, race_date, race_distance)
                            """
                        )
                    )
            else:
                # SQLite
                db.execute(
                    text(
                        """
                        CREATE TABLE race_plans (
                            id VARCHAR PRIMARY KEY,
                            user_id VARCHAR NOT NULL,
                            athlete_id INTEGER NOT NULL,
                            race_date TIMESTAMP NOT NULL,
                            race_distance VARCHAR NOT NULL,
                            race_name VARCHAR,
                            target_time VARCHAR,
                            priority VARCHAR NOT NULL DEFAULT 'A',
                            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
                # Create indexes
                db.execute(text("CREATE INDEX idx_race_plans_user_id ON race_plans(user_id)"))
                db.execute(text("CREATE INDEX idx_race_plans_athlete_id ON race_plans(athlete_id)"))
                db.execute(text("CREATE INDEX idx_race_plans_race_date ON race_plans(race_date)"))
                db.execute(text("CREATE INDEX idx_race_plan_athlete_priority ON race_plans(athlete_id, priority)"))
                # Create unique constraint
                db.execute(
                    text(
                        """
                        CREATE UNIQUE INDEX uq_race_plan_athlete_date_distance
                        ON race_plans(athlete_id, race_date, race_distance)
                        """
                    )
                )
            db.commit()
            logger.info("✓ Created race_plans table with indexes and constraints")
        else:
            logger.info("race_plans table already exists, checking for missing columns...")
            # Check if priority column exists (for existing tables)
            if not _column_exists(db, "race_plans", "priority"):
                logger.info("Adding priority column to race_plans table...")
                if _is_postgresql():
                    db.execute(text("ALTER TABLE race_plans ADD COLUMN priority VARCHAR NOT NULL DEFAULT 'A'"))
                else:
                    db.execute(text("ALTER TABLE race_plans ADD COLUMN priority VARCHAR NOT NULL DEFAULT 'A'"))
                db.commit()
                logger.info("✓ Added priority column to race_plans table")
            else:
                logger.info("priority column already exists in race_plans table")

            # Backfill existing rows to priority = 'A'
            logger.info("Backfilling existing race_plans with priority = 'A'...")
            db.execute(text("UPDATE race_plans SET priority = 'A' WHERE priority IS NULL OR priority = ''"))
            db.commit()
            logger.info("✓ Backfilled existing race_plans with priority = 'A'")

        # Step 3: Add active_race_id column to conversation_progress table
        if _table_exists(db, "conversation_progress"):
            if not _column_exists(db, "conversation_progress", "active_race_id"):
                logger.info("Adding active_race_id column to conversation_progress table...")
                if _is_postgresql():
                    db.execute(text("ALTER TABLE conversation_progress ADD COLUMN active_race_id VARCHAR"))
                    db.execute(text("CREATE INDEX idx_conversation_progress_active_race_id ON conversation_progress(active_race_id)"))
                else:
                    db.execute(text("ALTER TABLE conversation_progress ADD COLUMN active_race_id VARCHAR"))
                    db.execute(text("CREATE INDEX idx_conversation_progress_active_race_id ON conversation_progress(active_race_id)"))
                db.commit()
                logger.info("✓ Added active_race_id column to conversation_progress table")
            else:
                logger.info("active_race_id column already exists in conversation_progress table")
        else:
            logger.info("conversation_progress table does not exist, will be created by Base.metadata.create_all()")

        logger.info("Migration complete: multi-race season support added")
    except Exception as e:
        logger.exception(f"Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_multi_race_season()
