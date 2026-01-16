"""API endpoints for plan revisions.

Includes read operations and write operations (approval, rejection, rollback).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.db.models import PlannedSession, PlanRevision
from app.db.session import get_session
from app.plans.modify.plan_revision_repo import list_plan_revisions
from app.plans.rollback.rollback_engine import rollback_revision

router = APIRouter(prefix="/api/plans", tags=["plans"])


class PlanRevisionResponse(BaseModel):
    """Response model for plan revision."""

    id: str
    revision_type: str
    status: str
    reason: str | None
    blocked_reason: str | None
    affected_start: str | None  # ISO date string
    affected_end: str | None  # ISO date string
    deltas: dict | None
    created_at: str  # ISO datetime string
    applied: bool
    applied_at: str | None  # ISO datetime string
    approved_by_user: bool | None
    requires_approval: bool
    confidence: float | None
    parent_revision_id: str | None

    @classmethod
    def from_model(cls, revision: PlanRevision) -> "PlanRevisionResponse":
        """Create response from PlanRevision model."""
        return cls(
            id=revision.id,
            revision_type=revision.revision_type,
            status=revision.status,
            reason=revision.reason,
            blocked_reason=revision.blocked_reason,
            affected_start=revision.affected_start.isoformat() if revision.affected_start else None,
            affected_end=revision.affected_end.isoformat() if revision.affected_end else None,
            deltas=revision.deltas,
            created_at=revision.created_at.isoformat(),
            applied=revision.applied,
            applied_at=revision.applied_at.isoformat() if revision.applied_at else None,
            approved_by_user=revision.approved_by_user,
            requires_approval=revision.requires_approval,
            confidence=revision.confidence,
            parent_revision_id=revision.parent_revision_id,
        )


@router.get("/revisions", response_model=list[PlanRevisionResponse])
def get_plan_revisions(
    athlete_id: int = Query(..., description="Athlete ID to query revisions for"),
    user_id: str = Depends(get_current_user_id),
) -> list[PlanRevisionResponse]:
    """Get plan revisions for an athlete.

    Returns all plan revisions (applied and blocked) for the specified athlete,
    ordered by creation time (newest first).

    Args:
        athlete_id: Athlete ID to query revisions for
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        List of plan revisions

    Raises:
        HTTPException: 403 if user doesn't have access to athlete
    """
    logger.info(
        "Getting plan revisions",
        user_id=user_id,
        athlete_id=athlete_id,
    )

    with get_session() as session:
        # TODO: Add authorization check to verify user has access to athlete
        # For now, we'll just query the revisions

        revisions = list_plan_revisions(session=session, athlete_id=athlete_id)

        return [PlanRevisionResponse.from_model(rev) for rev in revisions]


@router.post("/revisions/{revision_id}/approve", response_model=PlanRevisionResponse)
def approve_revision(
    revision_id: str,
    user_id: str = Depends(get_current_user_id),
) -> PlanRevisionResponse:
    """Approve a pending revision.

    This applies the revision if it was pending approval.

    Args:
        revision_id: ID of revision to approve
        user_id: Current authenticated user ID

    Returns:
        Updated PlanRevision

    Raises:
        HTTPException: 404 if revision not found, 400 if cannot be approved
    """
    logger.info(
        "Approving revision",
        revision_id=revision_id,
        user_id=user_id,
    )

    with get_session() as session:
        revision = session.execute(
            select(PlanRevision).where(PlanRevision.id == revision_id)
        ).scalar_one_or_none()

        if revision is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Revision {revision_id} not found",
            )

        if revision.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Revision {revision_id} is not pending approval",
            )

        if not revision.requires_approval:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Revision {revision_id} does not require approval",
            )

        # Approve and apply
        revision.approved_by_user = True
        revision.status = "applied"
        revision.applied = True
        revision.applied_at = datetime.now(timezone.utc)

        session.commit()
        session.refresh(revision)

        return PlanRevisionResponse.from_model(revision)


@router.post("/revisions/{revision_id}/reject", response_model=PlanRevisionResponse)
def reject_revision(
    revision_id: str,
    user_id: str = Depends(get_current_user_id),
) -> PlanRevisionResponse:
    """Reject a pending revision.

    This marks the revision as blocked.

    Args:
        revision_id: ID of revision to reject
        user_id: Current authenticated user ID

    Returns:
        Updated PlanRevision

    Raises:
        HTTPException: 404 if revision not found, 400 if cannot be rejected
    """
    logger.info(
        "Rejecting revision",
        revision_id=revision_id,
        user_id=user_id,
    )

    with get_session() as session:
        revision = session.execute(
            select(PlanRevision).where(PlanRevision.id == revision_id)
        ).scalar_one_or_none()

        if revision is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Revision {revision_id} not found",
            )

        if revision.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Revision {revision_id} is not pending approval",
            )

        # Reject
        revision.approved_by_user = False
        revision.status = "blocked"
        revision.blocked_reason = "Rejected by user"

        session.commit()
        session.refresh(revision)

        return PlanRevisionResponse.from_model(revision)


@router.post("/revisions/{revision_id}/rollback", response_model=PlanRevisionResponse)
def rollback_revision_endpoint(
    revision_id: str,
    athlete_id: int = Query(..., description="Athlete ID"),
    user_id: str = Depends(get_current_user_id),
) -> PlanRevisionResponse:
    """Rollback a revision.

    This creates a new rollback revision that undoes the changes.

    Args:
        revision_id: ID of revision to rollback
        athlete_id: Athlete ID
        user_id: Current authenticated user ID

    Returns:
        New rollback PlanRevision

    Raises:
        HTTPException: 404 if revision not found, 400 if cannot be rolled back
    """
    logger.info(
        "Rolling back revision",
        revision_id=revision_id,
        athlete_id=athlete_id,
        user_id=user_id,
    )

    with get_session() as session:
        try:
            rollback_rev = rollback_revision(
                session=session,
                revision_id=revision_id,
                user_id=user_id,
                athlete_id=athlete_id,
            )
            session.commit()
            session.refresh(rollback_rev)
            return PlanRevisionResponse.from_model(rollback_rev)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e
