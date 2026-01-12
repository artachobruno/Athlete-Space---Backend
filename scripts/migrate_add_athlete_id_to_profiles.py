"""Migration script to add athlete_id column to athlete_profiles table.

This migration adds the athlete_id column to the athlete_profiles table
to match the SQLAlchemy model definition.

Usage:
    From project root:
    python scripts/migrate_add_athlete_id_to_profiles.py

    Or as a module:
    python -m scripts.migrate_add_athlete_id_to_profiles
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


def migrate_add_athlete_id_to_profiles() -> None:
    """Add athlete_id column to athlete_profiles table if it doesn't exist."""
    logger.info("Starting migration: add athlete_id column to athlete_profiles table")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    if _column_exists("athlete_profiles", "athlete_id"):
        logger.info("athlete_id column already exists in athlete_profiles table, skipping migration")
        return

    db = SessionLocal()
    try:
        logger.info("Adding athlete_id column to athlete_profiles table...")

        # Add athlete_id column (nullable initially so we can populate existing rows)
        if _is_postgresql():
            db.execute(
                text(
                    """
                    ALTER TABLE athlete_profiles
                    ADD COLUMN athlete_id INTEGER
                    """,
                ),
            )
        else:
            logger.warning("SQLite detected - athlete_id migration requires table recreation")
            logger.info("For SQLite, you may need to recreate the athlete_profiles table manually")
            db.rollback()
            return

        db.commit()

        # Populate athlete_id for existing rows from strava_accounts or strava_athlete_id
        logger.info("Populating athlete_id for existing athlete_profiles...")
        if _is_postgresql():
            # Try to get athlete_id from strava_accounts first
            # Note: strava_accounts.athlete_id is VARCHAR, so we cast it to integer
            db.execute(
                text(
                    """
                    UPDATE athlete_profiles ap
                    SET athlete_id = CAST(sa.athlete_id AS INTEGER)
                    FROM strava_accounts sa
                    WHERE ap.user_id = sa.user_id
                    AND ap.athlete_id IS NULL
                    AND sa.athlete_id ~ '^[0-9]+$'
                    """,
                ),
            )

            # Fallback: use strava_athlete_id if available
            db.execute(
                text(
                    """
                    UPDATE athlete_profiles
                    SET athlete_id = strava_athlete_id
                    WHERE athlete_id IS NULL
                    AND strava_athlete_id IS NOT NULL
                    """,
                ),
            )

            # Set to 0 for any remaining NULL values
            db.execute(
                text(
                    """
                    UPDATE athlete_profiles
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
                    CREATE INDEX IF NOT EXISTS idx_athlete_profiles_athlete_id
                    ON athlete_profiles (athlete_id)
                    """,
                ),
            )

            # Make athlete_id NOT NULL after population
            logger.info("Making athlete_id NOT NULL...")
            db.execute(
                text(
                    """
                    ALTER TABLE athlete_profiles
                    ALTER COLUMN athlete_id SET NOT NULL
                    """,
                ),
            )

            db.commit()
            logger.info("Successfully added athlete_id column to athlete_profiles table")
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
    migrate_add_athlete_id_to_profiles()
    logger.info("Migration completed successfully")
