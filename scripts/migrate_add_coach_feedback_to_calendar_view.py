"""Migration script to add coach_feedback to calendar_items view.

This migration updates the calendar_items view to LEFT JOIN coach_feedback
so that coach insight, instructions, and steps are available in the view.

Usage:
    From project root:
    python scripts/migrate_add_coach_feedback_to_calendar_view.py

    Or as a module:
    python -m scripts.migrate_add_coach_feedback_to_calendar_view
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
from sqlalchemy import create_engine, text

from app.config.settings import settings


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _view_exists(conn, view_name: str) -> bool:
    """Check if a view exists.

    Args:
        conn: Database connection
        view_name: Name of the view

    Returns:
        True if view exists, False otherwise
    """
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.views
                WHERE table_schema = 'public' AND table_name = :view_name
                """,
            ),
            {"view_name": view_name},
        ).fetchone()
        return result is not None
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='view' AND name=:view_name"),
        {"view_name": view_name},
    ).fetchone()
    return result is not None


def migrate_add_coach_feedback_to_calendar_view() -> None:
    """Add coach_feedback to calendar_items view payload."""
    logger.info("Starting migration: Add coach_feedback to calendar_items view")

    engine = create_engine(settings.database_url)
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"

    with engine.begin() as conn:
        view_exists = _view_exists(conn, "calendar_items")
        if not view_exists:
            logger.warning("calendar_items view does not exist. Skipping migration.")
            return

    logger.info("Updating calendar_items view to include coach_feedback...")

    with engine.begin() as conn:
        # Drop and recreate view with coach_feedback in payload
        conn.execute(text("DROP VIEW IF EXISTS calendar_items"))

        if _is_postgresql():
            conn.execute(text("""
                CREATE VIEW calendar_items AS
                SELECT
                  p.user_id,
                  p.id AS item_id,
                  'planned'::text AS kind,
                  p.starts_at,
                  p.ends_at,
                  p.sport,
                  p.title,
                  p.status,
                  jsonb_build_object(
                    'planned_session_id', p.id,
                    'workout_id', p.workout_id,
                    'distance_meters', p.distance_meters,
                    'duration_seconds', p.duration_seconds,
                    'execution_notes', p.execution_notes,
                    'must_dos', p.must_dos,
                    'tags', p.tags,
                    'coach_insight', cf.coach_insight,
                    'instructions', cf.instructions,
                    'steps', cf.steps
                  ) AS payload
                FROM planned_sessions p
                LEFT JOIN coach_feedback cf ON cf.planned_session_id = p.id

                UNION ALL

                SELECT
                  a.user_id,
                  a.id AS item_id,
                  'activity'::text AS kind,
                  a.starts_at,
                  a.ends_at,
                  a.sport,
                  a.title,
                  'completed'::text AS status,
                  jsonb_build_object(
                    'activity_id', a.id,
                    'source', a.source,
                    'distance_meters', a.distance_meters,
                    'duration_seconds', a.duration_seconds,
                    'tss', a.tss,
                    'metrics', a.metrics
                  ) AS payload
                FROM activities a;
            """))
        else:
            # SQLite version
            conn.execute(text("""
                CREATE VIEW calendar_items AS
                SELECT
                  p.user_id,
                  p.id AS item_id,
                  'planned' AS kind,
                  p.starts_at,
                  p.ends_at,
                  p.sport,
                  p.title,
                  p.status,
                  json_object(
                    'planned_session_id', p.id,
                    'workout_id', p.workout_id,
                    'distance_meters', p.distance_meters,
                    'duration_seconds', p.duration_seconds,
                    'execution_notes', p.execution_notes,
                    'must_dos', p.must_dos,
                    'tags', p.tags,
                    'coach_insight', cf.coach_insight,
                    'instructions', cf.instructions,
                    'steps', cf.steps
                  ) AS payload
                FROM planned_sessions p
                LEFT JOIN coach_feedback cf ON cf.planned_session_id = p.id

                UNION ALL

                SELECT
                  a.user_id,
                  a.id AS item_id,
                  'activity' AS kind,
                  a.starts_at,
                  a.ends_at,
                  a.sport,
                  a.title,
                  'completed' AS status,
                  json_object(
                    'activity_id', a.id,
                    'source', a.source,
                    'distance_meters', a.distance_meters,
                    'duration_seconds', a.duration_seconds,
                    'tss', a.tss,
                    'metrics', a.metrics
                  ) AS payload
                FROM activities a;
            """))
        logger.info("✓ Updated calendar_items view with coach_feedback")

    logger.info(f"Migration complete: calendar_items view updated ({db_type})")


if __name__ == "__main__":
    migrate_add_coach_feedback_to_calendar_view()
    logger.info("Migration completed successfully")
