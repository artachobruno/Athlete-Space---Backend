"""Migration script to create coach_feedback table.

This migration creates the coach_feedback table to persist LLM-generated
coach feedback (instructions, steps, coach_insight) for planned sessions.

Usage:
    From project root:
    python scripts/migrate_add_coach_feedback_table.py

    Or as a module:
    python -m scripts.migrate_add_coach_feedback_table
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


def _table_exists(db, table_name: str) -> bool:
    """Check if a table exists.

    Args:
        db: Database session
        table_name: Name of the table

    Returns:
        True if table exists, False otherwise
    """
    if _is_postgresql():
        result = db.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = :table_name
                """,
            ),
            {"table_name": table_name},
        ).fetchone()
        return result is not None
    result = db.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    ).fetchone()
    return result is not None


def migrate_add_coach_feedback_table() -> None:
    """Create coach_feedback table if it doesn't exist."""
    logger.info("Starting migration: create coach_feedback table")

    db = SessionLocal()
    try:
        if _table_exists(db, "coach_feedback"):
            logger.info("coach_feedback table already exists, skipping migration")
            return

        logger.info("Creating coach_feedback table")

        if _is_postgresql():
            db.execute(
                text(
                    """
                    CREATE TABLE coach_feedback (
                        id VARCHAR NOT NULL PRIMARY KEY,
                        planned_session_id VARCHAR NOT NULL UNIQUE,
                        user_id VARCHAR NOT NULL,
                        instructions JSONB NOT NULL DEFAULT '[]'::jsonb,
                        steps JSONB NOT NULL DEFAULT '[]'::jsonb,
                        coach_insight TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT fk_coach_feedback_planned_session
                            FOREIGN KEY (planned_session_id)
                            REFERENCES planned_sessions(id)
                            ON DELETE CASCADE,
                        CONSTRAINT fk_coach_feedback_user
                            FOREIGN KEY (user_id)
                            REFERENCES users(id)
                            ON DELETE CASCADE
                    )
                    """,
                ),
            )

            # Create indexes
            db.execute(
                text(
                    """
                    CREATE INDEX idx_coach_feedback_planned_session_id ON coach_feedback(planned_session_id)
                    """,
                ),
            )
            db.execute(
                text(
                    """
                    CREATE INDEX idx_coach_feedback_user_id ON coach_feedback(user_id)
                    """,
                ),
            )
            db.execute(
                text(
                    """
                    CREATE INDEX idx_coach_feedback_user_created ON coach_feedback(user_id, created_at)
                    """,
                ),
            )
        else:
            # SQLite
            db.execute(
                text(
                    """
                    CREATE TABLE coach_feedback (
                        id TEXT NOT NULL PRIMARY KEY,
                        planned_session_id TEXT NOT NULL UNIQUE,
                        user_id TEXT NOT NULL,
                        instructions TEXT NOT NULL DEFAULT '[]',
                        steps TEXT NOT NULL DEFAULT '[]',
                        coach_insight TEXT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (planned_session_id) REFERENCES planned_sessions(id) ON DELETE CASCADE,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    )
                    """,
                ),
            )

            # Create indexes
            db.execute(
                text(
                    """
                    CREATE INDEX idx_coach_feedback_planned_session_id ON coach_feedback(planned_session_id)
                    """,
                ),
            )
            db.execute(
                text(
                    """
                    CREATE INDEX idx_coach_feedback_user_id ON coach_feedback(user_id)
                    """,
                ),
            )
            db.execute(
                text(
                    """
                    CREATE INDEX idx_coach_feedback_user_created ON coach_feedback(user_id, created_at)
                    """,
                ),
            )

        db.commit()
        logger.info("Successfully created coach_feedback table")

    except Exception as e:
        logger.error(f"Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_add_coach_feedback_table()
    logger.info("Migration completed successfully")
