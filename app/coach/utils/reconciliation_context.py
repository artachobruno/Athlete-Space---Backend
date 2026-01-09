"""Reconciliation context for coach.

Provides reconciliation data (missed workouts, compliance, consistency)
for coach context builders. Uses reconciliation service to get authoritative
session statuses.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from loguru import logger

from app.calendar.reconciliation import SessionStatus
from app.calendar.reconciliation_service import reconcile_calendar


def get_reconciliation_stats(
    user_id: str,
    athlete_id: int,
    days: int = 30,
) -> dict[str, int | float]:
    """Get reconciliation statistics for coach context.

    Provides authoritative counts of:
    - Completed sessions
    - Missed sessions
    - Partial sessions
    - Substituted sessions
    - Compliance rate

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        days: Number of days to analyze (default: 30)

    Returns:
        Dictionary with statistics:
        {
            "completed_count": int,
            "missed_count": int,
            "partial_count": int,
            "substituted_count": int,
            "skipped_count": int,
            "total_planned": int,
            "compliance_rate": float,  # 0.0 - 1.0
        }
    """
    try:
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)

        results = reconcile_calendar(
            user_id=user_id,
            athlete_id=athlete_id,
            start_date=start_date,
            end_date=end_date,
        )

        completed_count = sum(1 for r in results if r.status == SessionStatus.COMPLETED)
        missed_count = sum(1 for r in results if r.status == SessionStatus.MISSED)
        partial_count = sum(1 for r in results if r.status == SessionStatus.PARTIAL)
        substituted_count = sum(1 for r in results if r.status == SessionStatus.SUBSTITUTED)
        skipped_count = sum(1 for r in results if r.status == SessionStatus.SKIPPED)
        total_planned = len(results)

        # Compliance rate: (completed + partial) / (total - skipped)
        # Skips are intentional, so exclude them from compliance calculation
        total_for_compliance = total_planned - skipped_count
        if total_for_compliance > 0:
            compliance_rate = (completed_count + partial_count) / total_for_compliance
        else:
            compliance_rate = 1.0 if total_planned == 0 else 0.0
    except Exception as e:
        logger.warning(f"Failed to get reconciliation stats: {e!r}, returning defaults")
        return {
            "completed_count": 0,
            "missed_count": 0,
            "partial_count": 0,
            "substituted_count": 0,
            "skipped_count": 0,
            "total_planned": 0,
            "compliance_rate": 0.0,
        }
    else:
        return {
            "completed_count": completed_count,
            "missed_count": missed_count,
            "partial_count": partial_count,
            "substituted_count": substituted_count,
            "skipped_count": skipped_count,
            "total_planned": total_planned,
            "compliance_rate": compliance_rate,
        }


def get_recent_missed_workouts(
    user_id: str,
    athlete_id: int,
    days: int = 14,
) -> list[dict[str, str]]:
    """Get list of recently missed workouts for coach context.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        days: Number of days to look back (default: 14)

    Returns:
        List of missed workout dictionaries:
        [
            {
                "session_id": str,
                "date": str,  # YYYY-MM-DD
                "type": str,
                "title": str,
            },
            ...
        ]
    """
    try:
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)

        results = reconcile_calendar(
            user_id=user_id,
            athlete_id=athlete_id,
            start_date=start_date,
            end_date=end_date,
        )

    except Exception as e:
        logger.warning(f"Failed to get missed workouts: {e!r}, returning empty list")
        return []
    else:
        return [
            {
                "session_id": r.session_id,
                "date": r.date,
                "type": "unknown",  # Type not in result, would need to fetch from PlannedSession
                "title": "Planned session",
            }
            for r in results
            if r.status == SessionStatus.MISSED
        ]
