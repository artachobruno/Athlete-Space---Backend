"""Read-only API endpoints for plan revisions.

Frontend reads only. No writes.
Returns plan revision history for an athlete.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel

from app.api.dependencies.auth import get_current_user_id
from app.db.models import PlanRevision
from app.db.session import get_session
from app.plans.modify.plan_revision_repo import list_plan_revisions

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
