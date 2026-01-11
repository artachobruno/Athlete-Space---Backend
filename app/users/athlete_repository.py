"""Repository for athlete data access.

Provides read-first access to athlete entities with lazy creation.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Athlete


class AthleteRepository:
    """Repository for athlete data access."""

    @staticmethod
    def get_or_create(session: Session, user_id: str) -> Athlete:
        """Get existing athlete or create new one for the given user.

        Args:
            session: Database session
            user_id: User ID (string UUID format)

        Returns:
            Athlete instance (existing or newly created)
        """
        athlete = session.query(Athlete).filter_by(user_id=user_id).one_or_none()
        if athlete:
            return athlete

        athlete = Athlete(user_id=user_id)
        session.add(athlete)
        session.commit()
        return athlete
