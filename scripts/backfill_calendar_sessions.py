"""Backfill script to create calendar sessions for existing activities.

This script materializes all existing activities into calendar_sessions.
It's idempotent - running it multiple times won't create duplicates.
"""

import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from sqlalchemy import select

from app.calendar.helpers import ensure_calendar_session_for_activity
from app.db.models import Activity, CalendarSession
from app.db.session import get_session


def backfill_calendar_sessions(user_id: str | None = None) -> dict[str, int]:
    """Backfill calendar sessions for existing activities.

    Args:
        user_id: Optional user_id to backfill for specific user only.
                 If None, backfills for all users.

    Returns:
        Dictionary with counts: {"processed": int, "created": int, "skipped": int, "errors": int}
    """
    logger.info("Starting calendar sessions backfill")
    if user_id:
        logger.info(f"Backfilling for user_id={user_id}")
    else:
        logger.info("Backfilling for all users")

    stats = {"processed": 0, "created": 0, "skipped": 0, "errors": 0}

    with get_session() as session:
        # Query activities
        query = select(Activity)
        if user_id:
            query = query.where(Activity.user_id == user_id)
        query = query.order_by(Activity.start_time.desc())

        activities = session.execute(query).scalars().all()
        total_count = len(activities)
        logger.info(f"Found {total_count} activities to process")

        for activity in activities:
            stats["processed"] += 1
            try:
                # Check if calendar session already exists
                existing = session.execute(
                    select(CalendarSession).where(CalendarSession.activity_id == activity.id)
                ).first()

                if existing:
                    stats["skipped"] += 1
                else:
                    # Use the helper function to create calendar session
                    ensure_calendar_session_for_activity(session, activity)
                    session.commit()
                    stats["created"] += 1

                if stats["processed"] % 100 == 0:
                    logger.info(f"Progress: {stats['processed']}/{total_count} activities processed")

            except Exception as e:
                stats["errors"] += 1
                logger.error(f"Error processing activity {activity.id}: {e}")
                session.rollback()
                # Continue with next activity
                continue

    logger.info(
        f"Backfill complete: processed={stats['processed']}, "
        f"created={stats['created']}, skipped={stats['skipped']}, errors={stats['errors']}"
    )
    return stats


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill calendar sessions for existing activities")
    parser.add_argument("--user-id", type=str, help="Optional user_id to backfill for specific user only")
    args = parser.parse_args()

    try:
        stats = backfill_calendar_sessions(user_id=args.user_id)
        print(f"Backfill complete: {stats}")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Backfill failed: {e}", exc_info=True)
        sys.exit(1)
