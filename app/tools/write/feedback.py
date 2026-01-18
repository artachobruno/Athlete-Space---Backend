"""Write tool for subjective feedback.

Athlete-reported subjective signals.
"""

from datetime import date

from loguru import logger
from sqlalchemy import select

from app.db.models import SubjectiveFeedback
from app.db.session import get_session


def record_subjective_feedback(
    user_id: str,
    day: date,
    fatigue: int | None = None,
    soreness: int | None = None,
    motivation: int | None = None,
    note: str | None = None,
) -> str:
    """Record athlete-reported subjective feedback.

    WRITE: Athlete-reported subjective signals.
    Executor-only. Coach may request, not execute.

    Args:
        user_id: User ID
        day: Date for feedback
        fatigue: Fatigue level (0-10, optional)
        soreness: Soreness level (0-10, optional)
        motivation: Motivation level (0-10, optional)
        note: Optional text note

    Returns:
        Confirmation message
    """
    logger.info(
        f"Recording subjective feedback: user_id={user_id}, date={day}, "
        f"fatigue={fatigue}, soreness={soreness}, motivation={motivation}"
    )

    with get_session() as session:
        # Check if feedback already exists for this date
        existing = session.execute(
            select(SubjectiveFeedback).where(
                SubjectiveFeedback.user_id == user_id,
                SubjectiveFeedback.date == day,
            )
        ).first()

        if existing:
            # Update existing feedback
            feedback = existing[0]
            if fatigue is not None:
                feedback.fatigue = fatigue
            if soreness is not None:
                feedback.soreness = soreness
            if motivation is not None:
                feedback.motivation = motivation
            if note is not None:
                feedback.note = note
        else:
            # Create new feedback
            feedback = SubjectiveFeedback(
                user_id=user_id,
                date=day,
                fatigue=fatigue,
                soreness=soreness,
                motivation=motivation,
                note=note,
            )
            session.add(feedback)

        session.commit()

    return f"Feedback recorded for {day.isoformat()}"
