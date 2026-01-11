"""Coach-scoped authentication dependency.

Provides current_coach dependency that ensures the authenticated user
has an associated coach entity.
"""

from __future__ import annotations

from fastapi import Depends

from app.api.dependencies.auth import get_current_user_id
from app.db.models import Coach
from app.db.session import get_session
from app.users.coach_repository import CoachRepository


def current_coach(
    user_id: str = Depends(get_current_user_id),
) -> Coach:
    """FastAPI dependency to get current coach for the authenticated user.

    Lazily creates a coach if one doesn't exist for the user.
    A user can have at most one coach record.

    Args:
        user_id: Current authenticated user ID (from get_current_user_id)

    Returns:
        Coach instance for the authenticated user
    """
    with get_session() as session:
        return CoachRepository.get_or_create(session, user_id)
