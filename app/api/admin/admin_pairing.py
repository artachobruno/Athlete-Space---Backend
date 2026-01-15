"""Admin endpoints for manual pairing operations.

Provides explicit APIs to manually merge or unmerge planned sessions
with executed activities. Manual actions override auto-pairing.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user_id
from app.db.session import get_db
from app.pairing.manual_pairing_service import manual_pair, manual_unpair

router = APIRouter(prefix="/admin/pairing", tags=["admin"])


class MergeRequest(BaseModel):
    """Request model for manual merge operation."""

    activity_id: str
    planned_session_id: str


class UnmergeRequest(BaseModel):
    """Request model for manual unmerge operation."""

    activity_id: str


@router.post("/merge")
def merge_pairing(
    payload: MergeRequest,
    session: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Manually merge an activity with a planned session.

    This operation:
    - Clears any existing pairing links (idempotent)
    - Creates bidirectional link between activity and planned session
    - Logs the decision for auditability
    - Overrides any auto-pairing

    Args:
        payload: Merge request with activity_id and planned_session_id
        session: Database session
        user_id: Current authenticated user ID

    Returns:
        Success message with IDs

    Raises:
        HTTPException: 401 if not authenticated
        HTTPException: 403 if user doesn't own the records
        HTTPException: 404 if activity or planned session not found
    """
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        manual_pair(
            activity_id=payload.activity_id,
            planned_session_id=payload.planned_session_id,
            user_id=user_id,
            session=session,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to merge pairing: {e!s}",
        ) from e

    return {
        "status": "success",
        "message": "Activity paired with planned session",
        "activity_id": payload.activity_id,
        "planned_session_id": payload.planned_session_id,
    }


@router.post("/unmerge")
def unmerge_pairing(
    payload: UnmergeRequest,
    session: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> dict[str, str]:
    """Manually unmerge an activity from its planned session.

    This operation:
    - Removes bidirectional link between activity and planned session
    - Logs the decision for auditability
    - Makes the activity and planned session available for re-pairing

    Args:
        payload: Unmerge request with activity_id
        session: Database session
        user_id: Current authenticated user ID

    Returns:
        Success message with activity ID

    Raises:
        HTTPException: 401 if not authenticated
        HTTPException: 403 if user doesn't own the activity
        HTTPException: 404 if activity not found
    """
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        manual_unpair(
            activity_id=payload.activity_id,
            user_id=user_id,
            session=session,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to unmerge pairing: {e!s}",
        ) from e

    return {
        "status": "success",
        "message": "Activity unpaired from planned session",
        "activity_id": payload.activity_id,
    }
