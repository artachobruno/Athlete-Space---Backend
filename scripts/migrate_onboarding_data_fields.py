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


def _add_column(db, table_name: str, column_name: str, column_type: str, nullable: bool = True) -> None:
    """Add a column to a table if it doesn't exist.

    Args:
        db: Database session
        table_name: Name of the table
        column_name: Name of the column
        column_type: SQL type for the column
        nullable: Whether the column is nullable
    """
    if _column_exists(db, table_name, column_name):
        logger.info(f"Column {table_name}.{column_name} already exists, skipping")
        return

    logger.info(f"Adding column {table_name}.{column_name}")
    null_clause = "" if nullable else " NOT NULL"
    db.execute(
        text(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {column_type}{null_clause}
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
            _add_column(db, "athlete_profiles", "strava_connected", "BOOLEAN", nullable=False)
            _add_column(db, "athlete_profiles", "sources", "JSONB", nullable=True)
            _add_column(db, "athlete_profiles", "onboarding_completed", "BOOLEAN", nullable=False)
            _add_column(db, "athlete_profiles", "strava_athlete_id", "INTEGER", nullable=True)
        else:
            _add_column(db, "athlete_profiles", "target_event", "JSON", nullable=True)
            _add_column(db, "athlete_profiles", "goals", "JSON", nullable=True)
            _add_column(db, "athlete_profiles", "name", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "email", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "date_of_birth", "TIMESTAMP", nullable=True)
            _add_column(db, "athlete_profiles", "location", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "unit_system", "TEXT", nullable=True)
            _add_column(db, "athlete_profiles", "strava_connected", "BOOLEAN", nullable=False)
            _add_column(db, "athlete_profiles", "sources", "JSON", nullable=True)
            _add_column(db, "athlete_profiles", "onboarding_completed", "BOOLEAN", nullable=False)
            _add_column(db, "athlete_profiles", "strava_athlete_id", "INTEGER", nullable=True)

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
