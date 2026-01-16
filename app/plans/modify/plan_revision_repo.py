"""Repository functions for plan revision persistence.

Handles creating and querying plan revisions.
Single responsibility: database operations only.
"""

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PlanRevision

if TYPE_CHECKING:
    from uuid import UUID


def create_plan_revision(
    session: Session,
    *,
    user_id: str,
    athlete_id: int,
    revision_type: str,
    status: str,
    reason: str | None = None,
    blocked_reason: str | None = None,
    affected_start: date | None = None,
    affected_end: date | None = None,
    deltas: dict | None = None,
) -> PlanRevision:
    """Create a plan revision record.

    Args:
        session: Database session
        user_id: User ID who made the modification
        athlete_id: Athlete ID whose plan was modified
        revision_type: Type of revision (modify_day, modify_week, modify_season, modify_race)
        status: Status of revision (applied, blocked)
        reason: Optional reason for modification
        blocked_reason: Optional reason if blocked
        affected_start: Start date of affected range
        affected_end: End date of affected range
        deltas: JSON field storing before/after snapshots and changes

    Returns:
        Created PlanRevision instance
    """
    revision = PlanRevision(
        user_id=user_id,
        athlete_id=athlete_id,
        revision_type=revision_type,
        status=status,
        reason=reason,
        blocked_reason=blocked_reason,
        affected_start=affected_start,
        affected_end=affected_end,
        deltas=deltas,
    )
    session.add(revision)
    session.flush()
    return revision


def list_plan_revisions(
    session: Session,
    *,
    athlete_id: int,
) -> list[PlanRevision]:
    """List plan revisions for an athlete, ordered by creation time (newest first).

    Args:
        session: Database session
        athlete_id: Athlete ID to query revisions for

    Returns:
        List of PlanRevision instances, ordered by created_at DESC
    """
    query = (
        select(PlanRevision)
        .where(PlanRevision.athlete_id == athlete_id)
        .order_by(PlanRevision.created_at.desc())
    )
    return list(session.execute(query).scalars().all())
