"""Migration script to add Garmin history tracking fields to user_integrations table.

This migration adds fields required for Garmin Summary Backfill API tracking:
- garmin_history_requested_at: Timestamp when history backfill was requested
- garmin_history_complete: Whether history backfill is complete
- garmin_last_webhook_received_at: Timestamp of last webhook received

Usage:
    From project root:
    python scripts/migrate_add_garmin_history_fields.py

    Or as a module:
    python -m scripts.migrate_add_garmin_history_fields
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
        default_value: Default value for the column (used when adding NOT NULL to existing table)
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


def migrate_add_garmin_history_fields() -> None:
    """Add Garmin history tracking fields to user_integrations table."""
    logger.info("Starting migration: add Garmin history tracking fields to user_integrations table")

    db = SessionLocal()
    try:
        if _is_postgresql():
            # PostgreSQL: Use TIMESTAMPTZ for timezone-aware timestamps
            _add_column(
                db,
                "user_integrations",
                "garmin_history_requested_at",
                "TIMESTAMPTZ",
                nullable=True,
            )
            _add_column(
                db,
                "user_integrations",
                "garmin_history_complete",
                "BOOLEAN",
                nullable=False,
                default_value="FALSE",
            )
            _add_column(
                db,
                "user_integrations",
                "garmin_last_webhook_received_at",
                "TIMESTAMPTZ",
                nullable=True,
            )

            # Add indexes for better query performance
            logger.info("Adding indexes for Garmin history fields")
            try:
                db.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_user_integrations_garmin_history_requested_at
                        ON user_integrations(garmin_history_requested_at)
                        WHERE garmin_history_requested_at IS NOT NULL
                        """
                    ),
                )
                logger.info("Added index on garmin_history_requested_at")
            except Exception as e:
                logger.warning(f"Could not create index on garmin_history_requested_at: {e}")

            try:
                db.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS idx_user_integrations_garmin_last_webhook_received_at
                        ON user_integrations(garmin_last_webhook_received_at)
                        WHERE garmin_last_webhook_received_at IS NOT NULL
                        """
                    ),
                )
                logger.info("Added index on garmin_last_webhook_received_at")
            except Exception as e:
                logger.warning(f"Could not create index on garmin_last_webhook_received_at: {e}")

        else:
            # SQLite: Use TEXT for timestamps
            _add_column(
                db,
                "user_integrations",
                "garmin_history_requested_at",
                "TEXT",
                nullable=True,
            )
            _add_column(
                db,
                "user_integrations",
                "garmin_history_complete",
                "BOOLEAN",
                nullable=False,
                default_value="0",
            )
            _add_column(
                db,
                "user_integrations",
                "garmin_last_webhook_received_at",
                "TEXT",
                nullable=True,
            )

        db.commit()
        logger.info("Successfully added Garmin history tracking fields to user_integrations table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_garmin_history_fields()
    logger.info("Migration completed successfully")
