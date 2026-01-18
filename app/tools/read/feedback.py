"""Read-only access to subjective feedback.

Athlete-reported subjective signals over time.
"""

from datetime import date

from loguru import logger
from sqlalchemy import select

from app.db.models import SubjectiveFeedback
from app.db.session import get_session


def get_subjective_feedback(
    user_id: str,
    start: date,
    end: date,
) -> list[SubjectiveFeedback]:
    """Get subjective feedback within a date range.

    READ-ONLY: Athlete-reported subjective signals.
    No modifications.

    Args:
        user_id: User ID
        start: Start date (inclusive)
        end: End date (inclusive)

    Returns:
        List of SubjectiveFeedback records, empty list if none found
    """
    logger.debug(f"Reading subjective feedback: user_id={user_id}, start={start}, end={end}")

    with get_session() as session:
        query = select(SubjectiveFeedback).where(
            SubjectiveFeedback.user_id == user_id,
            SubjectiveFeedback.date >= start,
            SubjectiveFeedback.date <= end,
        ).order_by(SubjectiveFeedback.date)

        feedback_list = list(session.execute(query).scalars().all())

        logger.debug(f"Found {len(feedback_list)} feedback entries")
        return feedback_list
