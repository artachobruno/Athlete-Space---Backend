"""Permission guards for coach-athlete access control.

Provides explicit permission checking for coach access to athlete data.
No implicit permissions - all access must be explicitly granted.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import CoachAthlete


def require_coach_access(
    session: Session,
    coach_id: str,
    athlete_id: str,
    require_edit: bool = False,
) -> CoachAthlete:
    """Require that a coach has access to an athlete.

    Checks the CoachAthlete join table to verify the relationship exists.
    Raises HTTPException 403 if access is denied.

    Args:
        session: Database session
        coach_id: Coach ID (string UUID format)
        athlete_id: Athlete ID (string UUID format)
        require_edit: If True, also requires can_edit=True (default: False)

    Returns:
        CoachAthlete relationship record

    Raises:
        HTTPException: 403 if coach is not assigned to athlete
        HTTPException: 403 if edit permission is required but not granted
    """
    link = session.query(CoachAthlete).filter_by(
        coach_id=coach_id,
        athlete_id=athlete_id,
    ).one_or_none()

    if not link:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coach not assigned to athlete",
        )

    if require_edit and not link.can_edit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Edit permission required",
        )

    return link
