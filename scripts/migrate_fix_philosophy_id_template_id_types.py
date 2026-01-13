"""Fix philosophy_id and template_id column types from UUID to VARCHAR.

This migration fixes the column types for philosophy_id and template_id in planned_sessions
table. These columns should store string identifiers (slugs/keys) like "5k_speed", "daniels",
not UUIDs.

If the columns are already VARCHAR, this migration is a no-op.
If the columns are UUID, they will be altered to VARCHAR.

Usage:
    From project root:
    python scripts/migrate_fix_philosophy_id_template_id_types.py

    Or as a module:
    python -m scripts.migrate_fix_philosophy_id_template_id_types
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


def _get_column_type(conn, table_name: str, column_name: str) -> str | None:
    """Get the data type of a column.

    Args:
        conn: Database connection
        table_name: Name of the table
        column_name: Name of the column

    Returns:
        Column data type (e.g., "uuid", "varchar", "text") or None if column doesn't exist
    """
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                AND table_name = :table_name
                AND column_name = :column_name
                """,
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        row = result.fetchone()
        return row[0].lower() if row else None

    # SQLite
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    for row in result.fetchall():
        if row[1] == column_name:
            return row[2].lower()
    return None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table.

    Args:
        conn: Database connection
        table_name: Name of the table
        column_name: Name of the column

    Returns:
        True if column exists, False otherwise
    """
    return _get_column_type(conn, table_name, column_name) is not None


def _alter_column_type(conn, table_name: str, column_name: str, new_type: str) -> None:
    """Alter the type of an existing column.

    Args:
        conn: Database connection
        table_name: Name of the table
        column_name: Name of the column
        new_type: New SQL type for the column
    """
    if _is_postgresql():
        # PostgreSQL: Use USING clause to cast existing values
        # For UUID -> VARCHAR, we need to cast via text
        logger.info(f"Altering column {table_name}.{column_name} to {new_type}...")
        conn.execute(
            text(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE {new_type} USING {column_name}::text"),
        )
        logger.info(f"Altered column {table_name}.{column_name} to {new_type}")
    else:
        # SQLite doesn't support ALTER COLUMN TYPE directly
        # This would require a more complex migration (create new table, copy data, drop old, rename)
        # For now, log a warning
        logger.warning(
            f"SQLite detected: Cannot alter column type for {table_name}.{column_name}. "
            f"SQLite doesn't support ALTER COLUMN TYPE. Manual migration may be required.",
        )


def migrate_fix_philosophy_id_template_id_types() -> None:
    """Fix philosophy_id and template_id column types from UUID to VARCHAR."""
    logger.info("Starting migration: fix philosophy_id and template_id column types")

    with engine.begin() as conn:
        columns_to_fix = ["philosophy_id", "template_id"]

        for column_name in columns_to_fix:
            if not _column_exists(conn, "planned_sessions", column_name):
                logger.info(f"Column planned_sessions.{column_name} does not exist, skipping")
                continue

            current_type = _get_column_type(conn, "planned_sessions", column_name)
            logger.info(
                f"Column planned_sessions.{column_name} current type: {current_type}",
            )

            if current_type is None:
                logger.warning(f"Could not determine type for planned_sessions.{column_name}, skipping")
                continue

            # Check if it's a UUID type (postgresql uses 'uuid', might also check for 'uuid' in type string)
            is_uuid_type = current_type == "uuid" or "uuid" in current_type.lower()

            if is_uuid_type:
                logger.info(
                    f"Column planned_sessions.{column_name} is UUID type, converting to VARCHAR...",
                )
                _alter_column_type(conn, "planned_sessions", column_name, "VARCHAR")
            elif current_type in {"varchar", "character varying", "text", "string"}:
                logger.info(
                    f"Column planned_sessions.{column_name} is already VARCHAR/text type, no change needed",
                )
            else:
                logger.warning(
                    f"Column planned_sessions.{column_name} has unexpected type '{current_type}', "
                    f"not altering. Manual review may be required.",
                )

    logger.info("Successfully fixed philosophy_id and template_id column types")


if __name__ == "__main__":
    migrate_fix_philosophy_id_template_id_types()
    logger.info("Migration completed successfully")
