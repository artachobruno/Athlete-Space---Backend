#!/usr/bin/env python3
"""Validate that database schema matches SQLAlchemy models.

This script compares the actual database schema with the SQLAlchemy model definitions
to catch schema mismatches before they cause runtime errors.

Usage:
    python scripts/validate_schema.py

Exit codes:
    0: Schema matches models
    1: Schema mismatch detected
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeMeta

from app.db.models import (
    Activity,
    AthleteProfile,
    Base,
    CoachMessage,
    DailyDecision,
    DailyTrainingLoad,
    PlannedSession,
    SeasonPlan,
    StravaAccount,
    StravaAuth,
    User,
    UserSettings,
    WeeklyIntent,
    WeeklyReport,
    WeeklyTrainingSummary,
)
from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def _get_db_columns(table_name: str) -> dict[str, dict[str, str | bool | None]]:
    """Get columns from actual database table.

    Returns:
        Dictionary mapping column_name -> {type, nullable, default}
    """
    with engine.connect() as conn:
        if _is_postgresql():
            result = conn.execute(
                text("""
                    SELECT column_name, data_type, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                    ORDER BY ordinal_position
                """),
                {"table_name": table_name},
            )
            columns = {}
            for row in result.fetchall():
                col_name, data_type, is_nullable, default = row
                columns[col_name] = {
                    "type": data_type.upper(),
                    "nullable": is_nullable == "YES",
                    "default": default,
                }
            return columns
        # SQLite
        result = conn.execute(text(f"PRAGMA table_info({table_name})"))
        columns = {}
        for row in result.fetchall():
            # SQLite PRAGMA: (cid, name, type, notnull, default_value, pk)
            col_name = row[1]
            col_type = row[2].upper() if row[2] else None
            not_null = bool(row[3])
            default = row[4]
            columns[col_name] = {
                "type": col_type,
                "nullable": not not_null,
                "default": default,
            }
        return columns


def _get_model_columns(model_class: type[Base]) -> dict[str, dict[str, str | bool | None]]:
    """Get columns from SQLAlchemy model.

    Returns:
        Dictionary mapping column_name -> {type, nullable, default}
    """
    columns = {}
    mapper = inspect(model_class)
    for column in mapper.columns:
        col_name = column.name
        # Map SQLAlchemy types to database types
        col_type = str(column.type).upper()
        # Normalize type names
        if "INTEGER" in col_type or "INT" in col_type:
            col_type = "INTEGER"
        elif "VARCHAR" in col_type or "STRING" in col_type or "TEXT" in col_type:
            col_type = "TEXT" if "sqlite" in str(engine.url).lower() else "VARCHAR"
        elif "BOOLEAN" in col_type or "BOOL" in col_type:
            col_type = "BOOLEAN"
        elif "DATETIME" in col_type or "TIMESTAMP" in col_type:
            col_type = "TIMESTAMP" if _is_postgresql() else "DATETIME"
        elif "JSON" in col_type:
            col_type = "JSON"
        elif "FLOAT" in col_type or "REAL" in col_type:
            col_type = "FLOAT"

        columns[col_name] = {
            "type": col_type,
            "nullable": column.nullable,
            "default": column.default.arg if column.default is not None else None,
        }
    return columns


def _validate_table(model_class: type[Base]) -> list[str]:
    """Validate that database table matches model.

    Returns:
        List of error messages (empty if validation passes)
    """
    errors = []
    table_name = model_class.__tablename__

    # Check if table exists
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        errors.append(f"Table '{table_name}' does not exist in database")
        return errors

    # Get columns from both sources
    db_columns = _get_db_columns(table_name)
    model_columns = _get_model_columns(model_class)

    # Check for missing columns in database
    missing_in_db = set(model_columns.keys()) - set(db_columns.keys())
    if missing_in_db:
        errors.append(f"Table '{table_name}': Missing columns in database: {sorted(missing_in_db)}. Run migrations to add these columns.")

    # Check for extra columns in database (warn but don't fail)
    extra_in_db = set(db_columns.keys()) - set(model_columns.keys())
    if extra_in_db:
        logger.warning(f"Table '{table_name}': Extra columns in database (not in model): {sorted(extra_in_db)}")

    return errors


def validate_schema(skip_if_no_db: bool = False) -> int:
    """Validate all model schemas against database.

    Args:
        skip_if_no_db: If True, return 0 (success) if database is unavailable.
                       If False, return 1 (error) if database is unavailable.

    Returns:
        0 if validation passes, 1 if errors found
    """
    logger.info("🔍 Validating database schema against SQLAlchemy models...")

    # Test database connection
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        if skip_if_no_db:
            logger.warning(f"⚠️ Cannot connect to database: {e}")
            logger.warning("Skipping schema validation (database unavailable)")
            return 0
        logger.error(f"❌ Cannot connect to database: {e}")
        logger.error("Schema validation requires a database connection")
        return 1

    all_errors = []

    # List of models to validate (focus on critical tables)
    models_to_validate = [
        User,
        StravaAuth,
        StravaAccount,  # This was the problematic one
        Activity,
        CoachMessage,
        DailyTrainingLoad,
        WeeklyTrainingSummary,
        PlannedSession,
        AthleteProfile,
        UserSettings,
    ]

    # Validate each model
    for model_class in models_to_validate:
        table_name = model_class.__tablename__
        logger.info(f"Validating table: {table_name}")
        errors = _validate_table(model_class)
        if errors:
            all_errors.extend(errors)
            for error in errors:
                logger.error(f"❌ {error}")
        else:
            logger.info(f"✅ Table '{table_name}' schema matches model")

    if all_errors:
        logger.error(f"\n❌ Schema validation failed with {len(all_errors)} error(s)")
        logger.error("\nTo fix:")
        logger.error("  1. Create a migration script in scripts/")
        logger.error("  2. Add the migration to app/main.py")
        logger.error("  3. Run: python scripts/run_migrations.py")
        return 1

    logger.info("\n✅ All database schemas match their models!")
    return 0


if __name__ == "__main__":
    # Allow skipping if DB unavailable (useful for pre-commit in environments without DB)
    skip_if_no_db = "--skip-if-no-db" in sys.argv
    sys.exit(validate_schema(skip_if_no_db=skip_if_no_db))
