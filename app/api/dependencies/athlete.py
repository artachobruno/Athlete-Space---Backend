"""Athlete-scoped authentication dependency.

Provides current_athlete dependency that ensures the authenticated user
has an associated athlete entity.
"""

from __future__ import annotations

from fastapi import Depends

from app.api.dependencies.auth import get_current_user_id
from app.db.session import get_session
from app.db.models import Athlete
from app.users.athlete_repository import AthleteRepository


def current_athlete(
    user_id: str = Depends(get_current_user_id),
) -> Athlete:
    """FastAPI dependency to get current athlete for the authenticated user.

    Lazily creates an athlete if one doesn't exist for the user.
    Every user has exactly one athlete.

    Args:
        user_id: Current authenticated user ID (from get_current_user_id)

    Returns:
        Athlete instance for the authenticated user
    """
    with get_session() as session:
        return AthleteRepository.get_or_create(session, user_id)
