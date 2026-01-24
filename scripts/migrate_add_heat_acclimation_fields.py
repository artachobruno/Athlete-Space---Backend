"""Migration script to add heat acclimation fields (v1.1).

Adds:
- heat_acclimation_score: REAL (NULL until computed)
- effective_heat_stress_index: REAL (NULL until computed)

Usage:
    From project root:
    python scripts/migrate_add_heat_acclimation_fields.py
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


def migrate_add_heat_acclimation_fields() -> None:
    """Add heat acclimation fields to activities table (v1.1)."""
    logger.info("Starting migration: add heat acclimation fields (v1.1)")

    db = SessionLocal()
    try:
        # Add heat acclimation columns
        heat_acclimation_columns = [
            ("heat_acclimation_score", "REAL"),
            ("effective_heat_stress_index", "REAL"),
        ]

        for column_name, column_type in heat_acclimation_columns:
            if not _column_exists(db, "activities", column_name):
                logger.info(f"Adding column {column_name} to activities table")
                db.execute(
                    text(f"ALTER TABLE activities ADD COLUMN {column_name} {column_type}")
                )
                logger.info(f"Added column {column_name} to activities table")
            else:
                logger.info(f"Column {column_name} already exists, skipping")

        db.commit()
        logger.info("Successfully added heat acclimation fields (v1.1)")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_heat_acclimation_fields()
    logger.info("Migration completed successfully")
