"""Migration script to add climate data tables and columns.

This migration creates:
- activity_climate_samples: Raw climate samples per activity
- Adds climate summary columns to activities table
- athlete_climate_profile: Athlete climate baseline

Usage:
    From project root:
    python scripts/migrate_add_climate_tables.py
"""

from __future__ import annotations

import sys
from pathlib import Path

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
    """Check if table exists."""
    if _is_postgresql():
        result = db.execute(
            text(
                """
                SELECT tablename FROM pg_tables
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
    """Check if column exists in table."""
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
    result = db.execute(
        text("PRAGMA table_info(:table_name)"),
        {"table_name": table_name},
    ).fetchall()
    return any(col[1] == column_name for col in result)


def migrate_add_climate_tables() -> None:
    """Add climate data tables and columns."""
    logger.info("Starting migration: add climate data tables and columns")

    db = SessionLocal()
    try:
        is_postgres = _is_postgresql()

        # 1. Create activity_climate_samples table
        if not _table_exists(db, "activity_climate_samples"):
            logger.info("Creating activity_climate_samples table")
            if is_postgres:
                # Detect actual type of activities.id
                activity_id_type_result = db.execute(
                    text(
                        """
                        SELECT data_type
                        FROM information_schema.columns
                        WHERE table_name = 'activities'
                        AND column_name = 'id'
                        """
                    )
                ).fetchone()
                
                activity_id_type = "VARCHAR"
                if activity_id_type_result:
                    db_type = activity_id_type_result[0]
                    if db_type in ("uuid", "character varying"):
                        if db_type == "uuid":
                            activity_id_type = "UUID"
                        else:
                            activity_id_type = "VARCHAR"
                    logger.info(f"Detected activities.id type: {db_type}, using {activity_id_type} for foreign key")
                
                db.execute(
                    text(
                        f"""
                        CREATE TABLE activity_climate_samples (
                          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                          activity_id {activity_id_type} NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
                          sample_time TIMESTAMP WITH TIME ZONE NOT NULL,
                          lat DOUBLE PRECISION,
                          lon DOUBLE PRECISION,
                          temperature_c REAL,
                          humidity_pct REAL,
                          dew_point_c REAL,
                          wind_speed_mps REAL,
                          wind_direction_deg REAL,
                          precip_mm REAL,
                          source TEXT,
                          created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                        )
                        """
                    )
                )
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_activity_climate_samples_activity_time
                        ON activity_climate_samples (activity_id, sample_time)
                        """
                    )
                )
            else:
                # SQLite
                db.execute(
                    text(
                        """
                        CREATE TABLE activity_climate_samples (
                          id TEXT PRIMARY KEY,
                          activity_id TEXT NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
                          sample_time TIMESTAMP NOT NULL,
                          lat REAL,
                          lon REAL,
                          temperature_c REAL,
                          humidity_pct REAL,
                          dew_point_c REAL,
                          wind_speed_mps REAL,
                          wind_direction_deg REAL,
                          precip_mm REAL,
                          source TEXT,
                          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
                db.execute(
                    text(
                        """
                        CREATE INDEX idx_activity_climate_samples_activity_time
                        ON activity_climate_samples (activity_id, sample_time)
                        """
                    )
                )
            logger.info("Created activity_climate_samples table")
        else:
            logger.info("activity_climate_samples table already exists, skipping")

        # 2. Add climate columns to activities table
        climate_columns = [
            ("has_climate_data", "BOOLEAN DEFAULT FALSE"),
            ("avg_temperature_c", "REAL"),
            ("max_temperature_c", "REAL"),
            ("avg_dew_point_c", "REAL"),
            ("max_dew_point_c", "REAL"),
            ("wind_avg_mps", "REAL"),
            ("precip_total_mm", "REAL"),
            ("heat_stress_index", "REAL"),
            ("conditions_label", "TEXT"),
            ("heat_tss_adjustment_pct", "REAL"),
            ("adjusted_tss", "REAL"),
            ("climate_model_version", "TEXT"),
        ]

        for column_name, column_type in climate_columns:
            if not _column_exists(db, "activities", column_name):
                logger.info(f"Adding column {column_name} to activities table")
                db.execute(
                    text(f"ALTER TABLE activities ADD COLUMN {column_name} {column_type}")
                )
                logger.info(f"Added column {column_name} to activities table")
            else:
                logger.info(f"Column {column_name} already exists, skipping")

        # 3. Create athlete_climate_profile table
        if not _table_exists(db, "athlete_climate_profile"):
            logger.info("Creating athlete_climate_profile table")
            if is_postgres:
                # Detect actual type of users.id
                user_id_type_result = db.execute(
                    text(
                        """
                        SELECT data_type
                        FROM information_schema.columns
                        WHERE table_name = 'users'
                        AND column_name = 'id'
                        """
                    )
                ).fetchone()
                
                user_id_type = "VARCHAR"
                if user_id_type_result:
                    db_type = user_id_type_result[0]
                    if db_type in ("uuid", "character varying"):
                        if db_type == "uuid":
                            user_id_type = "UUID"
                        else:
                            user_id_type = "VARCHAR"
                    logger.info(f"Detected users.id type: {db_type}, using {user_id_type} for foreign key")
                
                db.execute(
                    text(
                        f"""
                        CREATE TABLE athlete_climate_profile (
                          athlete_id {user_id_type} PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                          home_lat DOUBLE PRECISION,
                          home_lon DOUBLE PRECISION,
                          climate_type TEXT,
                          avg_summer_temp_c REAL,
                          avg_summer_dew_point_c REAL,
                          created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                          updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
                        )
                        """
                    )
                )
            else:
                # SQLite
                db.execute(
                    text(
                        """
                        CREATE TABLE athlete_climate_profile (
                          athlete_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                          home_lat REAL,
                          home_lon REAL,
                          climate_type TEXT,
                          avg_summer_temp_c REAL,
                          avg_summer_dew_point_c REAL,
                          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                        """
                    )
                )
            logger.info("Created athlete_climate_profile table")
        else:
            logger.info("athlete_climate_profile table already exists, skipping")

        db.commit()
        logger.info("Successfully added climate data tables and columns")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_climate_tables()
    logger.info("Migration completed successfully")
