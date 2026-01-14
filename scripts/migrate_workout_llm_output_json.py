"""Migration: Ensure workouts.llm_output_json is JSONB.

Phase B9: Fix DB schema to ensure llm_output_json column is JSONB type.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import text

from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return engine.dialect.name == "postgresql"


def _table_exists(conn, table_name: str) -> bool:
    """Check if table exists."""
    if _is_postgresql():
        result = conn.execute(
            text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)"
            ),
            {"table_name": table_name},
        )
        return result.scalar() is True
    # SQLite
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    )
    return result.first() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if column exists."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_name = :table_name AND column_name = :column_name
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.scalar() is True
    # SQLite
    result = conn.execute(
        text("PRAGMA table_info(:table_name)"),
        {"table_name": table_name},
    )
    columns = result.fetchall()
    return any(col[1] == column_name for col in columns)


def _get_column_type(conn, table_name: str, column_name: str) -> str | None:
    """Get column type."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_name = :table_name AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        row = result.first()
        return row[0] if row else None
    # SQLite doesn't distinguish JSON/JSONB
    return "TEXT"


def migrate_workout_llm_output_json() -> None:
    """Ensure workouts.llm_output_json is JSONB (PostgreSQL) or JSON (SQLite).

    This migration:
    1. Checks if column exists
    2. If PostgreSQL and column is not JSONB, alters it to JSONB
    3. If SQLite, ensures it's JSON type (which is TEXT in SQLite)
    """
    logger.info("Starting workout llm_output_json migration")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.begin() as conn:
        # Check if workouts table exists
        if not _table_exists(conn, "workouts"):
            logger.warning("workouts table does not exist, skipping migration")
            return

        # Check if column exists
        if not _column_exists(conn, "workouts", "llm_output_json"):
            logger.info("llm_output_json column does not exist, creating it...")
            if _is_postgresql():
                conn.execute(
                    text("ALTER TABLE workouts ADD COLUMN llm_output_json JSONB")
                )
            else:
                conn.execute(
                    text("ALTER TABLE workouts ADD COLUMN llm_output_json TEXT")
                )
            logger.info("llm_output_json column created successfully")
        else:
            # Column exists, check type
            column_type = _get_column_type(conn, "workouts", "llm_output_json")
            logger.info(f"llm_output_json column type: {column_type}")

            if _is_postgresql():
                # PostgreSQL: ensure it's JSONB
                if column_type not in {"jsonb", "json"}:
                    logger.warning(
                        f"llm_output_json is {column_type}, not JSONB. "
                        "Manual migration may be required."
                    )
                elif column_type == "json":
                    logger.info("Converting llm_output_json from JSON to JSONB...")
                    conn.execute(
                        text("ALTER TABLE workouts ALTER COLUMN llm_output_json TYPE JSONB USING llm_output_json::jsonb")
                    )
                    logger.info("llm_output_json converted to JSONB successfully")
                else:
                    logger.info("llm_output_json is already JSONB")
            else:
                # SQLite: JSON is stored as TEXT, which is fine
                logger.info("llm_output_json column exists (SQLite uses TEXT for JSON)")

    logger.info("Workout llm_output_json migration completed successfully")


if __name__ == "__main__":
    migrate_workout_llm_output_json()
