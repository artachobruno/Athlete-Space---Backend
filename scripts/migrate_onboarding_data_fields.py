# ⚠️ LEGACY: One-time migration script - not included in run_migrations.py
# Keep for historical reference and potential database recovery scenarios
"""Migration script to add onboarding data fields to user_settings and athlete_profiles tables.

This migration adds fields needed to support full onboarding data collection:
- user_settings: injury_notes, consistency, goal
- athlete_profiles: target_event, goals, name, email, date_of_birth, location,
  unit_system, strava_connected, sources, onboarding_completed, strava_athlete_id

Usage:
    From project root:
    python scripts/migrate_onboarding_data_fields.py

    Or as a module:
    python -m scripts.migrate_onboarding_data_fields
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


def _add_column(
    db,
    table_name: str,
    column_name: str,
    column_type: str,
    nullable: bool = True,
    default_value: str | None = None,
) -> None:
    """Add a column to a table if it doesn't exist.

    Args:
        db: Database session
        table_name: Name of the table
        column_name: Name of the column
        column_type: SQL type for the column
        nullable: Whether the column is nullable
        default_value: Default value for the column (required for NOT NULL columns on tables with existing rows)
    """
    if _column_exists(db, table_name, column_name):
        logger.info(f"Column {table_name}.{column_name} already exists, skipping")
        return

    logger.info(f"Adding column {table_name}.{column_name}")
    null_clause = "" if nullable else " NOT NULL"
    default_clause = f" DEFAULT {default_value}" if default_value is not None else ""
    db.execute(
        text(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {column_type}{default_clause}{null_clause}
            """,
        ),
    )


def migrate_onboarding_data_fields() -> None:
    """Add onboarding data fields to user_settings and athlete_profiles tables."""
    logger.info("Starting migration: add onboarding data fields")

    db = SessionLocal()
    try:
        # Add fields to user_settings table
        logger.info("Adding fields to user_settings table")
        if _is_postgresql():
            _add_column(db, "user_settings", "injury_notes", "TEXT", nullable=True)
            _add_column(db, "user_settings", "consistency", "VARCHAR(100)", nullable=True)
            _add_column(db, "user_settings", "goal", "VARCHAR(200)", nullable=True)
        else:
            _add_column(db, "user_settings", "injury_notes", "TEXT", nullable=True)
            _add_column(db, "user_settings", "consistency", "TEXT", nullable=True)
            _add_column(db, "user_settings", "goal", "TEXT", nullable=True)

        # Add fields to athlete_profiles table
        logger.info("Adding fields to athlete_profiles table")
        if _is_postgresql():
            _add_column(db, "athlete_profiles", "target_event", "JSONB", nullable=True)
            _add_column(db, "athlete_profiles", "goals", "JSONB", nullable=True)
            _add_column(db, "athlete_profiles", "name", "VARCHAR(255)", nullable=True)
            _add_column(db, "athlete_profiles", "email", "VARCHAR(255)", nullable=True)
            _add_column(db, "athlete_profiles", "date_of_birth", "TIMESTAMP", nullable=True)
            _add_column(db, "athlete_profiles", "location", "VARCHAR(255)", nullable=True)
            _add_column(db, "athlete_profiles", "unit_system", "VARCHAR(20)", nullable=True)
            _add_column(db, "athlete_profiles", "strava_connected", "BOOLEAN", nullable=False, default_value="FALSE")
            _add_column(db, "athlete_profiles", "sources", "JSONB", nullable=True)
            _add_column(db, "athlete_profiles", "onboarding_completed", "BOOLEAN", nullable=False, default_value="FALSE")
            _add_column(db, "athlete_profiles", "strava_athlete_id", "INTEGER", nullable=True)
            _add_column(db, "athlete_profiles", "years_training", "INTEGER", nullable=True)
            _add_column(db, "athlete_profiles", "primary_sport", "VARCHAR(255)", nullable=True)
            _add_column(db, "athlete_profiles", "primary_goal", "VARCHAR(255)", nullable=True)
        else:
            _add_column(db, "athlete_profiles", "target_event", "JSON", nullable=True)
            _add_column(db, "athlete_profiles", "goals", "JSON", nullable=True)
            _add_column(db, "athlete_profiles", "name", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "email", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "date_of_birth", "TIMESTAMP", nullable=True)
            _add_column(db, "athlete_profiles", "location", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "unit_system", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "strava_connected", "BOOLEAN", nullable=False, default_value="0")
            _add_column(db, "athlete_profiles", "sources", "JSON", nullable=True)
            _add_column(db, "athlete_profiles", "onboarding_completed", "BOOLEAN", nullable=False, default_value="0")
            _add_column(db, "athlete_profiles", "strava_athlete_id", "INTEGER", nullable=True)
            _add_column(db, "athlete_profiles", "years_training", "INTEGER", nullable=True)
            _add_column(db, "athlete_profiles", "primary_sport", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "primary_goal", "TEXT", nullable=True)

        # Set default values for boolean fields
        if _is_postgresql():
            db.execute(
                text(
                    """
                    UPDATE athlete_profiles
                    SET strava_connected = FALSE
                    WHERE strava_connected IS NULL
                    """,
                ),
            )
            db.execute(
                text(
                    """
                    UPDATE athlete_profiles
                    SET onboarding_completed = FALSE
                    WHERE onboarding_completed IS NULL
                    """,
                ),
            )
        else:
            db.execute(
                text(
                    """
                    UPDATE athlete_profiles
                    SET strava_connected = 0
                    WHERE strava_connected IS NULL
                    """,
                ),
            )
            db.execute(
                text(
                    """
                    UPDATE athlete_profiles
                    SET onboarding_completed = 0
                    WHERE onboarding_completed IS NULL
                    """,
                ),
            )

        db.commit()
        logger.info("Successfully added onboarding data fields")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_onboarding_data_fields()
    logger.info("Migration completed successfully")
