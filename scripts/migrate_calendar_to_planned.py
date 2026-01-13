"""Backfill script to migrate calendar_sessions to planned_sessions.

This script migrates existing calendar_sessions rows to planned_sessions.
Each calendar_session row becomes one planned_session row.

Rules:
- 1 row → 1 planned_session
- Preserve timestamps
- Do NOT dedupe
- Do NOT infer
- Create workout via factory
"""

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

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select, text

from app.config.settings import settings
from app.db.models import PlannedSession, StravaAccount
from app.db.session import get_session
from app.workouts.workout_factory import WorkoutFactory


def _is_postgresql() -> bool:
    """Check if database is PostgreSQL."""
    return "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower()


def _table_exists(conn, table_name: str) -> bool:
    """Check if table exists (database-agnostic)."""
    if _is_postgresql():
        result = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return result.scalar() is True
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
        {"table_name": table_name},
    )
    return result.fetchone() is not None


def _get_athlete_id(session, user_id: str) -> int | None:
    """Get athlete_id from user_id via StravaAccount."""
    account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
    if account:
        try:
            return int(account[0].athlete_id)
        except (ValueError, TypeError):
            return None
    return None


def migrate_calendar_to_planned(user_id: str | None = None) -> dict[str, int]:
    """Migrate calendar_sessions to planned_sessions.

    Args:
        user_id: Optional user_id to migrate for specific user only.
                 If None, migrates for all users.

    Returns:
        Dictionary with counts: {"processed": int, "created": int, "skipped": int, "errors": int}
    """
    logger.info("Starting calendar_sessions to planned_sessions migration")
    if user_id:
        logger.info(f"Migrating for user_id={user_id}")
    else:
        logger.info("Migrating for all users")

    stats = {"processed": 0, "created": 0, "skipped": 0, "errors": 0}

    with get_session() as session:
        # Check if calendar_sessions table exists
        conn = session.connection()
        if not _table_exists(conn, "calendar_sessions"):
            logger.info("calendar_sessions table does not exist. Nothing to migrate.")
            return stats

        # Query calendar_sessions
        query = text("SELECT * FROM calendar_sessions")
        if user_id:
            query = text("SELECT * FROM calendar_sessions WHERE user_id = :user_id")
            result = session.execute(query, {"user_id": user_id})
        else:
            result = session.execute(query)

        rows = result.fetchall()
        column_names = result.keys() if hasattr(result, "keys") else []

        # Build column index map
        col_map: dict[str, int] = {}
        if column_names:
            col_map.update({col_name: idx for idx, col_name in enumerate(column_names)})
        else:
            # Fallback: assume standard column order
            col_map = {
                "id": 0,
                "user_id": 1,
                "date": 2,
                "type": 3,
                "title": 4,
                "duration_minutes": 5,
                "distance_km": 6,
                "status": 7,
                "activity_id": 8,
                "created_at": 9,
                "updated_at": 10,
            }

        total_count = len(rows)
        logger.info(f"Found {total_count} calendar_sessions to migrate")

        for row in rows:
            stats["processed"] += 1
            try:
                # Extract fields from row
                row_dict: dict[str, str | int | float | datetime | None] = {}
                for col_name, idx in col_map.items():
                    if idx < len(row):
                        row_dict[col_name] = row[idx]

                cal_user_id = str(row_dict.get("user_id", ""))
                if not cal_user_id:
                    logger.warning(f"Skipping row with missing user_id: {row_dict.get('id')}")
                    stats["skipped"] += 1
                    continue

                # Get athlete_id
                athlete_id = _get_athlete_id(session, cal_user_id)
                if athlete_id is None:
                    logger.warning(f"Skipping row for user_id={cal_user_id}: no athlete_id found")
                    stats["skipped"] += 1
                    continue

                # Check if planned_session already exists (by checking if we've already migrated this)
                # We'll use the calendar_session id as a marker, or check by date+title
                cal_date = row_dict.get("date")
                if not cal_date or not isinstance(cal_date, datetime):
                    logger.warning(f"Skipping row with invalid date: {row_dict.get('id')}")
                    stats["skipped"] += 1
                    continue

                cal_title = str(row_dict.get("title", ""))
                if not cal_title:
                    logger.warning(f"Skipping row with missing title: {row_dict.get('id')}")
                    stats["skipped"] += 1
                    continue

                # Check if already migrated (by date + title + user)
                existing = session.execute(
                    select(PlannedSession).where(
                        PlannedSession.user_id == cal_user_id,
                        PlannedSession.athlete_id == athlete_id,
                        PlannedSession.date == cal_date,
                        PlannedSession.title == cal_title,
                    )
                ).scalar_one_or_none()

                if existing:
                    logger.debug(f"Skipping already migrated session: {cal_title} on {cal_date.date()}")
                    stats["skipped"] += 1
                    continue

                # Extract time from date if it has time component
                cal_time: str | None = None
                if isinstance(cal_date, datetime) and cal_date.time() != cal_date.time().replace(hour=0, minute=0, second=0):
                    cal_time = cal_date.strftime("%H:%M")

                # Get status (default to "completed" if status is "completed", otherwise "planned")
                cal_status = str(row_dict.get("status", "planned"))
                if cal_status not in {"planned", "completed", "skipped", "cancelled"}:
                    cal_status = "completed" if cal_status == "completed" else "planned"

                # Create planned_session
                planned_session = PlannedSession(
                    user_id=cal_user_id,
                    athlete_id=athlete_id,
                    date=cal_date,
                    time=cal_time,
                    type=str(row_dict.get("type", "Run")),
                    title=cal_title,
                    duration_minutes=row_dict.get("duration_minutes"),
                    distance_km=row_dict.get("distance_km"),
                    intensity=None,  # Not available in calendar_sessions
                    notes=None,  # Not available in calendar_sessions
                    plan_type="migrated",  # Mark as migrated
                    plan_id=None,
                    week_number=None,
                    session_order=None,
                    phase=None,
                    source="calendar_migration",
                    philosophy_id=None,
                    template_id=None,
                    session_type=None,
                    distance_mi=None,
                    tags=None,
                    status=cal_status,
                    completed=(cal_status == "completed"),
                    completed_at=cal_date if cal_status == "completed" else None,
                    completed_activity_id=str(row_dict.get("activity_id")) if row_dict.get("activity_id") else None,
                    workout_id=None,  # Will be set by factory
                )

                # Preserve timestamps if available
                if row_dict.get("created_at") and isinstance(row_dict["created_at"], datetime):
                    planned_session.created_at = row_dict["created_at"]
                if row_dict.get("updated_at") and isinstance(row_dict["updated_at"], datetime):
                    planned_session.updated_at = row_dict["updated_at"]

                session.add(planned_session)
                session.flush()  # Ensure ID is generated

                # Create workout via factory
                WorkoutFactory.get_or_create_for_planned_session(session, planned_session)

                session.commit()
                stats["created"] += 1

                if stats["processed"] % 100 == 0:
                    logger.info(f"Progress: {stats['processed']}/{total_count} rows processed")

            except Exception as e:
                stats["errors"] += 1
                logger.error(f"Error processing calendar_session row: {e}", exc_info=True)
                session.rollback()
                # Continue with next row
                continue

    logger.info(
        f"Migration complete: processed={stats['processed']}, "
        f"created={stats['created']}, skipped={stats['skipped']}, errors={stats['errors']}"
    )
    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate calendar_sessions to planned_sessions")
    parser.add_argument("--user-id", type=str, help="Optional user_id to migrate for specific user only")
    args = parser.parse_args()

    try:
        stats = migrate_calendar_to_planned(user_id=args.user_id)
        print(f"Migration complete: {stats}")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)
