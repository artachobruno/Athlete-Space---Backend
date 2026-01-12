# ⚠️ LEGACY: One-time migration script - not included in run_migrations.py
# Keep for historical reference and potential database recovery scenarios
"""Migration script to add streams_data column to activities table.

This migration adds a JSON column to store time-series streams data from Strava
(GPS coordinates, heart rate, power, cadence, speed, etc.) for activities.

Usage:
    From project root:
    python scripts/migrate_add_streams_data.py

    Or as a module:
    python -m scripts.migrate_add_streams_data
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
from app.db.session import SessionLocal


def migrate_add_streams_data() -> None:
    """Add streams_data column to activities table."""
    logger.info("Starting migration: add streams_data column to activities table")

    db = SessionLocal()
    try:
        # Check if column already exists
        if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower():
            # PostgreSQL
            result = db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'activities' AND column_name = 'streams_data'
                    """
                )
            ).fetchone()
            column_exists = result is not None
        else:
            # SQLite
            result = db.execute(text("PRAGMA table_info(activities)")).fetchall()
            column_exists = any(col[1] == "streams_data" for col in result)

        if column_exists:
            logger.info("Column streams_data already exists, skipping migration")
            return

        # Add column
        logger.info("Adding streams_data column to activities table")
        if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower():
            # PostgreSQL: Use JSONB for better performance
            db.execute(
                text(
                    """
                    ALTER TABLE activities
                    ADD COLUMN streams_data JSONB
                    """
                )
            )
        else:
            # SQLite: Use JSON (SQLite 3.38+ supports JSON)
            db.execute(
                text(
                    """
                    ALTER TABLE activities
                    ADD COLUMN streams_data JSON
                    """
                )
            )

        db.commit()
        logger.info("Successfully added streams_data column to activities table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_streams_data()
    logger.info("Migration completed successfully")
