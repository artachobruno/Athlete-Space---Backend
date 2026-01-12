"""Sync planned_sessions table schema with model.

This migration adds missing columns to planned_sessions table:
- philosophy_id: UUID
- template_id: UUID
- session_type: VARCHAR
- distance_mi: FLOAT
- tags: JSONB

Usage:
    From project root:
    python scripts/migrate_sync_planned_sessions_schema.py

    Or as a module:
    python -m scripts.migrate_sync_planned_sessions_schema
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

from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in str(engine.url).lower() or "postgres" in str(engine.url).lower()


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table.

    Args:
        conn: Database connection
        table_name: Name of the table
        column_name: Name of the column

    Returns:
        True if column exists, False otherwise
    """
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = :table_name
                AND column_name = :column_name
                """,
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None
    # SQLite
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    columns = [row[1] for row in result.fetchall()]
    return column_name in columns


def _add_column_if_missing(conn, table_name: str, column_name: str, column_type: str, nullable: bool = True) -> None:
    """Add a column to a table if it doesn't exist.

    Args:
        conn: Database connection
        table_name: Name of the table
        column_name: Name of the column
        column_type: SQL type for the column
        nullable: Whether the column is nullable
    """
    if _column_exists(conn, table_name, column_name):
        logger.info(f"Column {table_name}.{column_name} already exists, skipping")
        return

    logger.info(f"Adding column {table_name}.{column_name} ({column_type})...")
    nullable_clause = "" if nullable else " NOT NULL"
    if _is_postgresql():
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}{nullable_clause}"))
    else:
        # SQLite doesn't support NOT NULL on ALTER TABLE without default
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
    logger.info(f"Added column {table_name}.{column_name}")


def migrate_sync_planned_sessions_schema() -> None:
    """Sync planned_sessions table schema with model."""
    logger.info("Starting migration: sync planned_sessions schema")

    with engine.begin() as conn:
        _add_column_if_missing(conn, "planned_sessions", "philosophy_id", "UUID")
        _add_column_if_missing(conn, "planned_sessions", "template_id", "UUID")
        _add_column_if_missing(conn, "planned_sessions", "session_type", "VARCHAR")
        _add_column_if_missing(conn, "planned_sessions", "distance_mi", "FLOAT")
        # JSONB for PostgreSQL, JSON for SQLite
        column_type = "JSONB" if _is_postgresql() else "JSON"
        _add_column_if_missing(conn, "planned_sessions", "tags", column_type)

    logger.info("Successfully synced planned_sessions schema")


if __name__ == "__main__":
    migrate_sync_planned_sessions_schema()
    logger.info("Migration completed successfully")
