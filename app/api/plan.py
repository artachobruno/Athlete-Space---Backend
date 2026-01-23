"""Plan inspection API endpoints (dev/admin only).

Provides diagnostic views of plan intent, phase logic, weekly structure,
coach reasoning, and plan modifications.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin.utils import require_admin
from app.api.dependencies.auth import get_current_user_id
from app.db.models import StravaAccount
from app.db.session import get_session
from app.schemas.plan_inspect import PlanInspectResponse
from app.services.plan_inspector import inspect_plan

router = APIRouter(prefix="/plan", tags=["plan"])


@router.get("/inspect", response_model=PlanInspectResponse)
async def get_plan_inspect(
    user_id: str = Depends(get_current_user_id),
    athlete_id: int | None = Query(None, description="Athlete ID (optional, defaults to user's athlete)"),
    horizon: str | None = Query(
        None, description="Horizon for evaluation: week, season, or race (defaults to season)"
    ),
    preview: bool = Query(False, description="Include preview of proposed changes"),
) -> PlanInspectResponse:
    """Get plan inspection data (dev/admin only).

    This endpoint exposes plan intent, phase logic, weekly structure,
    coach reasoning, and plan modifications for diagnostic purposes.

    Args:
        user_id: Current authenticated user ID
        athlete_id: Optional athlete ID (defaults to user's athlete)

    Returns:
        PlanInspectResponse with inspection data

    Raises:
        HTTPException: 403 if not admin/dev, 404 if athlete not found, 400 if no plan
    """
    logger.info("Plan inspect requested", user_id=user_id, athlete_id=athlete_id)

    # Require admin/dev access
    with get_session() as session:
        require_admin(user_id, session)

    # Get athlete_id if not provided
    if not athlete_id:
        with get_session() as session:
            account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
            if not account:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Strava account not connected",
                )
            athlete_id = int(account[0].athlete_id)

    try:
        return inspect_plan(athlete_id=athlete_id, user_id=user_id, horizon=horizon, preview=preview)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        logger.exception("Failed to inspect plan", user_id=user_id, athlete_id=athlete_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to inspect plan",
        ) from e
