"""Migration script to add athlete_id column to planned_sessions table.

This migration adds the athlete_id column to the planned_sessions table
to match the SQLAlchemy model definition.

Usage:
    From project root:
    python scripts/migrate_add_athlete_id_to_planned_sessions.py

    Or as a module:
    python -m scripts.migrate_add_athlete_id_to_planned_sessions
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path (must be absolute for Render/production)
script_dir = Path(__file__).parent.resolve()
project_root = script_dir.parent.resolve()

# Verify project root contains app directory or pyproject.toml
if not (project_root / "app").exists() and not (project_root / "pyproject.toml").exists():
    # If parent doesn't have app/ or pyproject.toml, try current working directory
    cwd = Path.cwd().resolve()
    if (cwd / "app").exists() or (cwd / "pyproject.toml").exists():
        project_root = cwd
    else:
        # Last resort: try going up one more level (for cases where script is in src/scripts/)
        parent_parent = script_dir.parent.parent.resolve()
        if (parent_parent / "app").exists() or (parent_parent / "pyproject.toml").exists():
            project_root = parent_parent

# Ensure project root is in path
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


def migrate_add_athlete_id_to_planned_sessions() -> None:
    """Add athlete_id column to planned_sessions table if it doesn't exist."""
    logger.info("Starting migration: add athlete_id column to planned_sessions table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    if _column_exists("planned_sessions", "athlete_id"):
        logger.info("athlete_id column already exists in planned_sessions table, skipping migration")
        return

    db = SessionLocal()
    try:
        logger.info("Adding athlete_id column to planned_sessions table...")

        # Add athlete_id column (nullable initially so we can populate existing rows)
        if _is_postgresql():
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ADD COLUMN athlete_id INTEGER
                    """,
                ),
            )
        else:
            logger.warning("SQLite detected - athlete_id migration requires table recreation")
            logger.info("For SQLite, you may need to recreate the planned_sessions table manually")
            db.rollback()
            return

        db.commit()

        # Populate athlete_id for existing rows from athlete_profiles
        logger.info("Populating athlete_id for existing planned_sessions...")
        if _is_postgresql():
            # Try to get athlete_id from athlete_profiles table first
            db.execute(
                text(
                    """
                    UPDATE planned_sessions ps
                    SET athlete_id = ap.athlete_id
                    FROM athlete_profiles ap
                    WHERE ps.user_id = ap.user_id
                    AND ps.athlete_id IS NULL
                    """,
                ),
            )

            # Fallback: use strava_accounts if athlete_profiles doesn't have athlete_id yet
            # Note: sa.athlete_id is BIGINT, so we cast directly (no regex check needed for numeric type)
            db.execute(
                text(
                    """
                    UPDATE planned_sessions ps
                    SET athlete_id = CAST(sa.athlete_id AS INTEGER)
                    FROM strava_accounts sa
                    WHERE ps.user_id = sa.user_id
                    AND ps.athlete_id IS NULL
                    AND sa.athlete_id IS NOT NULL
                    """,
                ),
            )

            # Set to 0 for any remaining NULL values
            db.execute(
                text(
                    """
                    UPDATE planned_sessions
                    SET athlete_id = 0
                    WHERE athlete_id IS NULL
                    """,
                ),
            )

            db.commit()

            # Create index on athlete_id
            logger.info("Creating index on athlete_id...")
            db.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_planned_sessions_athlete_id
                    ON planned_sessions (athlete_id)
                    """,
                ),
            )

            # Make athlete_id NOT NULL after population
            logger.info("Making athlete_id NOT NULL...")
            db.execute(
                text(
                    """
                    ALTER TABLE planned_sessions
                    ALTER COLUMN athlete_id SET NOT NULL
                    """,
                ),
            )

            db.commit()
            logger.info("Successfully added athlete_id column to planned_sessions table")
        else:
            logger.warning("SQLite migration not fully implemented")
            db.rollback()

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_athlete_id_to_planned_sessions()
    logger.info("Migration completed successfully")
