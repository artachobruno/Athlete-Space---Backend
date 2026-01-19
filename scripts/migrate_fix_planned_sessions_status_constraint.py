"""Migration script to update planned_sessions status CHECK constraint to use 'cancelled'.

The database constraint currently only allows 'canceled' (one 'l'), but the API and frontend
use 'cancelled' (two 'l's). This migration updates any existing 'canceled' records to 'cancelled'
and updates the constraint to only allow 'cancelled'.

Usage:
    From project root:
    python scripts/migrate_fix_planned_sessions_status_constraint.py

    Or as a module:
    python -m scripts.migrate_fix_planned_sessions_status_constraint
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


def _constraint_exists(db, constraint_name: str) -> bool:
    """Check if a constraint exists.

    Args:
        db: Database session
        constraint_name: Name of the constraint

    Returns:
        True if constraint exists, False otherwise
    """
    if _is_postgresql():
        result = db.execute(
            text(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE constraint_name = :constraint_name
                """,
            ),
            {"constraint_name": constraint_name},
        ).fetchone()
        return result is not None
    # SQLite doesn't have named CHECK constraints in the same way
    # We'll check if the table exists and has the status column
    result = db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='planned_sessions'")).fetchone()
    return result is not None


def migrate_fix_planned_sessions_status_constraint() -> None:
    """Update planned_sessions status CHECK constraint to use 'cancelled'."""
    logger.info("Starting migration: update planned_sessions status constraint to use 'cancelled'")

    db = SessionLocal()
    try:
        if not _is_postgresql():
            logger.warning("SQLite detected - CHECK constraints are handled differently in SQLite")
            logger.info("For SQLite, the constraint is enforced at the application level")
            logger.info("Migration complete (SQLite - no database changes needed)")
            return

        # Find the constraint name - check for status-related CHECK constraints
        constraint_name = None

        # First, try to find any CHECK constraint on the status column
        result = db.execute(
            text(
                """
                SELECT tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.constraint_column_usage ccu
                    ON tc.constraint_name = ccu.constraint_name
                WHERE tc.table_name = 'planned_sessions'
                AND tc.constraint_type = 'CHECK'
                AND ccu.column_name = 'status'
                """,
            ),
        ).fetchall()

        if result:
            constraint_name = result[0][0]
            logger.info(f"Found constraint: {constraint_name}")
        else:
            # Try the standard naming convention
            constraint_name = "planned_sessions_status_check"
            logger.info(f"Trying standard constraint name: {constraint_name}")
            if not _constraint_exists(db, constraint_name):
                # Try to find any CHECK constraint on the table
                result = db.execute(
                    text(
                        """
                        SELECT constraint_name
                        FROM information_schema.table_constraints
                        WHERE table_name = 'planned_sessions'
                        AND constraint_type = 'CHECK'
                        """,
                    ),
                ).fetchall()
                if result:
                    constraint_name = result[0][0]
                    logger.info(f"Found CHECK constraint (may be status-related): {constraint_name}")
                else:
                    logger.error("Could not find planned_sessions status constraint")
                    logger.info("The constraint may not exist or may have a different name.")
                    logger.info("Proceeding to create new constraint with standard name.")
                    constraint_name = "planned_sessions_status_check"

        # First, migrate any existing 'canceled' records to 'cancelled'
        logger.info("Migrating any existing 'canceled' records to 'cancelled'...")
        try:
            result = db.execute(
                text(
                    """
                    UPDATE planned_sessions
                    SET status = 'cancelled'
                    WHERE status = 'canceled'
                    """,
                ),
            )
            rows_updated = result.rowcount
            if rows_updated > 0:
                logger.info(f"✓ Updated {rows_updated} record(s) from 'canceled' to 'cancelled'")
            else:
                logger.info("No records with 'canceled' status found")
        except Exception as e:
            logger.error(f"Error migrating records: {e}")
            db.rollback()
            raise

        # Drop the old constraint
        logger.info(f"Dropping old constraint: {constraint_name}")
        try:
            db.execute(
                text(
                    f"""
                    ALTER TABLE planned_sessions
                    DROP CONSTRAINT IF EXISTS {constraint_name}
                    """,
                ),
            )
            logger.info(f"✓ Dropped constraint: {constraint_name}")
        except Exception as e:
            logger.error(f"Error dropping constraint: {e}")
            db.rollback()
            raise

        # Create new constraint that only allows 'cancelled' (not 'canceled')
        new_constraint_name = "planned_sessions_status_check"
        logger.info(f"Creating new constraint: {new_constraint_name}")
        try:
            db.execute(
                text(
                    f"""
                    ALTER TABLE planned_sessions
                    ADD CONSTRAINT {new_constraint_name}
                    CHECK (status IN ('planned', 'completed', 'skipped', 'moved', 'cancelled'))
                    """,
                ),
            )
            logger.info(f"✓ Created new constraint: {new_constraint_name}")
        except Exception as e:
            logger.error(f"Error creating new constraint: {e}")
            db.rollback()
            raise

        db.commit()
        logger.info("Successfully updated planned_sessions status constraint to use 'cancelled'")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_fix_planned_sessions_status_constraint()
    logger.info("Migration completed successfully")
