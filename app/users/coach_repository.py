"""Repository for coach data access.

Provides read-first access to coach entities with lazy creation.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Coach


class CoachRepository:
    """Repository for coach data access."""

    @staticmethod
    def get_or_create(session: Session, user_id: str) -> Coach:
        """Get existing coach or create new one for the given user.

        Args:
            session: Database session
            user_id: User ID (string UUID format)

        Returns:
            Coach instance (existing or newly created)
        """
        coach = session.query(Coach).filter_by(user_id=user_id).one_or_none()
        if coach:
            return coach

        coach = Coach(user_id=user_id)
        session.add(coach)
        session.commit()
        return coach
