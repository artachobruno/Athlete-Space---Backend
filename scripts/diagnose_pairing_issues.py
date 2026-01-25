"""Diagnostic script to understand why activities aren't pairing.

This script shows detailed information about unpaired activities and planned sessions
to help diagnose pairing issues.

Usage:
    python scripts/diagnose_pairing_issues.py [--user-id USER_ID] [--days DAYS]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, PlannedSession
from app.db.session import SessionLocal
from app.pairing.session_links import get_link_for_activity, get_link_for_planned


def diagnose_pairing(
    db: Session,
    user_id: str | None = None,
    days: int | None = None,
) -> None:
    """Diagnose why activities aren't pairing.

    Args:
        db: Database session
        user_id: Optional user ID to filter by
        days: Optional number of days to look back
    """
    # Get unpaired activities
    activities_query = select(Activity).where(Activity.user_id.isnot(None))

    if user_id:
        activities_query = activities_query.where(Activity.user_id == user_id)

    if days:
        cutoff_date = datetime.now(UTC) - timedelta(days=days)
        activities_query = activities_query.where(Activity.starts_at >= cutoff_date)

    all_activities = list(db.scalars(activities_query).all())
    unpaired_activities = [a for a in all_activities if not get_link_for_activity(db, a.id)]

    # Get unpaired planned sessions
    planned_query = select(PlannedSession).where(
        PlannedSession.status.notin_(["deleted", "skipped", "completed"]),
    )

    if user_id:
        planned_query = planned_query.where(PlannedSession.user_id == user_id)

    if days:
        cutoff_date = datetime.now(UTC) - timedelta(days=days)
        planned_query = planned_query.where(PlannedSession.starts_at >= cutoff_date)

    all_planned = list(db.scalars(planned_query).all())
    unpaired_planned = [p for p in all_planned if not get_link_for_planned(db, p.id)]

    logger.info("=" * 80)
    logger.info("PAIRING DIAGNOSTICS")
    logger.info("=" * 80)
    logger.info(f"Unpaired Activities: {len(unpaired_activities)}")
    logger.info(f"Unpaired Planned Sessions: {len(unpaired_planned)}")
    logger.info("")

    # Group by date
    activities_by_date: dict[str, list[Activity]] = {}
    for activity in unpaired_activities:
        if activity.starts_at:
            date_str = activity.starts_at.date().isoformat()
            if date_str not in activities_by_date:
                activities_by_date[date_str] = []
            activities_by_date[date_str].append(activity)

    planned_by_date: dict[str, list[PlannedSession]] = {}
    for planned in unpaired_planned:
        if planned.starts_at:
            date_str = planned.starts_at.date().isoformat()
            if date_str not in planned_by_date:
                planned_by_date[date_str] = []
            planned_by_date[date_str].append(planned)

    # Find dates with both activities and planned sessions
    common_dates = set(activities_by_date.keys()) & set(planned_by_date.keys())

    logger.info(f"Dates with both unpaired activities AND planned sessions: {len(common_dates)}")
    logger.info("")

    for date_str in sorted(common_dates):
        logger.info(f"📅 {date_str}")
        logger.info("  Activities:")
        for activity in activities_by_date[date_str]:
            duration_str = f"{activity.duration_seconds/60:.1f}min" if activity.duration_seconds else "no duration"
            logger.info(
                f"    - {activity.id[:8]}... {activity.sport} {duration_str} "
                f"(starts_at={activity.starts_at})"
            )

        logger.info("  Planned Sessions:")
        for planned in planned_by_date[date_str]:
            duration_str = f"{planned.duration_minutes}min" if planned.duration_minutes else "no duration"
            logger.info(
                f"    - {planned.id[:8]}... {planned.type} {duration_str} "
                f"(starts_at={planned.starts_at})"
            )
        logger.info("")

    # Show dates with only activities
    activity_only_dates = set(activities_by_date.keys()) - set(planned_by_date.keys())
    if activity_only_dates:
        logger.info(f"Dates with ONLY activities (no planned sessions): {len(activity_only_dates)}")
        for date_str in sorted(list(activity_only_dates)[:5]):  # Show first 5
            logger.info(f"  {date_str}: {len(activities_by_date[date_str])} activities")

    # Show dates with only planned sessions
    planned_only_dates = set(planned_by_date.keys()) - set(activities_by_date.keys())
    if planned_only_dates:
        logger.info(f"Dates with ONLY planned sessions (no activities): {len(planned_only_dates)}")
        for date_str in sorted(list(planned_only_dates)[:5]):  # Show first 5
            logger.info(f"  {date_str}: {len(planned_by_date[date_str])} planned sessions")

    logger.info("=" * 80)


def main() -> None:
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Diagnose why activities aren't pairing with planned sessions"
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Filter by specific user ID (optional)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Only process activities from the last N days (default: 30)",
    )

    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("Pairing Diagnostics Script")
    if args.user_id:
        logger.info(f"Filter: user_id={args.user_id}")
    logger.info(f"Filter: last {args.days} days")
    logger.info("=" * 80)

    db = SessionLocal()
    try:
        diagnose_pairing(
            db=db,
            user_id=args.user_id,
            days=args.days,
        )

    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
