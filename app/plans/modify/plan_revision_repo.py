"""Repository functions for plan revision persistence.

Handles creating and querying plan revisions.
Single responsibility: database operations only.
"""

from datetime import date, datetime, timezone
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
    confidence: float | None = None,
    requires_approval: bool = False,
    parent_revision_id: str | None = None,
) -> PlanRevision:
    """Create a plan revision record.

    Args:
        session: Database session
        user_id: User ID who made the modification
        athlete_id: Athlete ID whose plan was modified
        revision_type: Type of revision (modify_day, modify_week, modify_season, modify_race, rollback)
        status: Status of revision (applied, blocked, pending)
        reason: Optional reason for modification
        blocked_reason: Optional reason if blocked
        affected_start: Start date of affected range
        affected_end: End date of affected range
        deltas: JSON field storing before/after snapshots and changes
        confidence: Confidence score (0.0-1.0) for this revision
        requires_approval: Whether this revision requires user approval
        parent_revision_id: ID of parent revision (for rollbacks)

    Returns:
        Created PlanRevision instance
    """
    applied = status == "applied"
    applied_at = datetime.now(timezone.utc) if applied else None

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
        applied=applied,
        applied_at=applied_at,
        requires_approval=requires_approval,
        confidence=confidence,
        parent_revision_id=parent_revision_id,
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


def list_regenerations(
    session: Session,
    athlete_id: int,
) -> list[PlanRevision]:
    """List plan regenerations for an athlete, ordered by creation time (newest first).

    Args:
        session: Database session
        athlete_id: Athlete ID to query regenerations for

    Returns:
        List of PlanRevision instances with revision_type="regenerate_plan",
        ordered by created_at DESC
    """
    query = (
        select(PlanRevision)
        .where(
            PlanRevision.athlete_id == athlete_id,
            PlanRevision.revision_type == "regenerate_plan",
        )
        .order_by(PlanRevision.created_at.desc())
    )
    return list(session.execute(query).scalars().all())
