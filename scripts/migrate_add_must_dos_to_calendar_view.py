"""Migration script to add must_dos to calendar_items view.

This migration updates the calendar_items view to include must_dos
in the payload for planned sessions.
"""

import sys
from pathlib import Path

# Add project root to Python path
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
from app.db.session import engine


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _view_exists(conn, view_name: str) -> bool:
    """Check if view exists."""
    if _is_postgresql():
        result = conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM information_schema.views WHERE table_name = :view_name)"),
            {"view_name": view_name}
        ).scalar()
        return bool(result)
    # SQLite
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='view' AND name=:view_name"),
        {"view_name": view_name}
    ).first()
    return result is not None


def migrate_add_must_dos_to_calendar_view() -> None:
    """Add must_dos to calendar_items view payload.

    Updates the calendar_items view to include must_dos field
    in the payload JSONB for planned sessions.
    """
    logger.info("Starting migration: Add must_dos to calendar_items view")
    db_type = "PostgreSQL" if _is_postgresql() else "SQLite"
    logger.info(f"Database type: {db_type}")

    with engine.connect() as conn:
        view_exists = _view_exists(conn, "calendar_items")

    if not view_exists:
        logger.warning("calendar_items view does not exist. Skipping migration.")
        return

    logger.info("Updating calendar_items view to include must_dos...")

    with engine.begin() as conn:
        # Drop and recreate view with must_dos
        conn.execute(text("DROP VIEW IF EXISTS calendar_items"))

        # Recreate view with must_dos in payload
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
                    'tags', p.tags
                  ) AS payload
                FROM planned_sessions p

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
                    'tags', p.tags
                  ) AS payload
                FROM planned_sessions p

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
        logger.info("✓ Updated calendar_items view with must_dos")

    logger.info(f"Migration complete: calendar_items view updated ({db_type})")


if __name__ == "__main__":
    migrate_add_must_dos_to_calendar_view()
