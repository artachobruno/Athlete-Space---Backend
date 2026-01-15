#!/usr/bin/env python3
"""Check status of planned sessions for a specific date.

Usage:
    python scripts/check_planned_session_status.py [date]

    If no date is provided, defaults to 2025-01-16 (tomorrow).
    Date format: YYYY-MM-DD
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.db.models import PlannedSession, User
from app.db.session import get_session


def _raise_invalid_date_format(target_date: str) -> None:
    """Raise ValueError for invalid date format."""
    logger.error(f"❌ Invalid date format: {target_date}. Expected YYYY-MM-DD format.")


def check_planned_session_status(target_date: str | None = None) -> int:
    """Check status of planned sessions for a specific date.

    Args:
        target_date: Date in YYYY-MM-DD format. If None, defaults to 2025-01-16.

    Returns:
        0 if successful, 1 if errors found
    """
    if target_date is None:
        target_date = "2025-01-16"

    try:
        # Parse date components manually to avoid naive datetime warning
        parts = target_date.split("-")
        if len(parts) != 3:
            _raise_invalid_date_format(target_date)
            return 1
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        parsed_date = date(year, month, day)
    except (ValueError, IndexError):
        _raise_invalid_date_format(target_date)
        return 1

    logger.info(f"🔍 Checking planned sessions for date: {parsed_date}")

    try:
        with get_session() as session:
            # Convert date to datetime range (start and end of day in UTC)
            start_datetime = datetime.combine(parsed_date, datetime.min.time()).replace(tzinfo=UTC)
            end_datetime = datetime.combine(parsed_date, datetime.max.time()).replace(tzinfo=UTC)

            # Query all planned sessions for this date
            result = session.execute(
                select(PlannedSession, User)
                .join(User, PlannedSession.user_id == User.id)
                .where(
                    PlannedSession.date >= start_datetime,
                    PlannedSession.date <= end_datetime,
                )
                .order_by(PlannedSession.date, PlannedSession.time)
            )

            sessions = result.all()

            if not sessions:
                logger.info(f"📭 No planned sessions found for {parsed_date}")
                return 0

            logger.info(f"📊 Found {len(sessions)} planned session(s) for {parsed_date}\n")

            for planned_session, user in sessions:
                logger.info("=" * 80)
                logger.info(f"Session ID: {planned_session.id}")
                logger.info(f"User: {user.email if hasattr(user, 'email') else user.id}")
                logger.info(f"Title: {planned_session.title}")
                logger.info(f"Type: {planned_session.type}")
                logger.info(f"Date: {planned_session.date}")
                logger.info(f"Time: {planned_session.time or 'Not set'}")
                logger.info(f"Status: {planned_session.status}")
                logger.info(f"Completed (boolean): {planned_session.completed}")
                logger.info(f"Completed At: {planned_session.completed_at or 'Not set'}")
                logger.info(f"Completed Activity ID: {planned_session.completed_activity_id or 'Not set'}")
                logger.info(f"Workout ID: {planned_session.workout_id or 'Not set'}")
                logger.info("")

            logger.info("=" * 80)
            logger.info("✅ Status check completed successfully")
            return 0

    except Exception as e:
        logger.exception(f"❌ Error checking planned sessions: {e}")
        return 1


if __name__ == "__main__":
    target_date = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(check_planned_session_status(target_date))
