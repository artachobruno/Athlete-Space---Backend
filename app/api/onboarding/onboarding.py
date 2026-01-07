"""Onboarding API routes.

HTTP boundary for onboarding endpoints. Contains only FastAPI routing logic.
All business logic lives in app.onboarding.service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import func, select

from app.api.dependencies.auth import get_current_user_id
from app.db.models import Activity, StravaAccount
from app.db.session import get_session
from app.onboarding.schemas import OnboardingCompleteRequest, OnboardingCompleteResponse
from app.onboarding.service import complete_onboarding_flow

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


@router.get("/status")
def get_onboarding_status(
    user_id: str = Depends(get_current_user_id),
):
    """Get onboarding status and data availability.

    This endpoint checks if:
    1. Strava is connected
    2. Activity data has been synced from Strava
    3. Onboarding can proceed (has data to populate)

    Args:
        user_id: Current authenticated user ID

    Returns:
        Dictionary with:
        - connected: bool - Whether Strava is connected
        - has_data: bool - Whether activity data exists
        - activity_count: int - Number of activities synced
        - ready: bool - Whether onboarding can proceed (has data)
    """
    logger.info(f"Onboarding status check for user_id={user_id}")

    with get_session() as session:
        # Check if Strava is connected
        account_result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        connected = account_result is not None

        if not connected:
            return {
                "connected": False,
                "has_data": False,
                "activity_count": 0,
                "ready": False,
            }

        # Check if activity data exists
        activity_count_result = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar()
        activity_count = activity_count_result if activity_count_result is not None else 0
        has_data = activity_count > 0

        logger.info(f"Onboarding status for user_id={user_id}: connected={connected}, has_data={has_data}, activity_count={activity_count}")

        return {
            "connected": True,
            "has_data": has_data,
            "activity_count": activity_count,
            "ready": has_data,  # Ready when we have data to populate
        }


@router.post("/complete", response_model=OnboardingCompleteResponse)
def complete_onboarding(
    request: OnboardingCompleteRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Complete onboarding process.

    This endpoint:
    1. Persists onboarding data (profile and training preferences)
    2. Normalizes structured fields
    3. Runs LLM-based attribute extraction from goals
    4. Conditionally generates plans (if user opted in)
    5. Returns response to frontend

    Args:
        request: Onboarding completion request
        user_id: Current authenticated user ID

    Returns:
        OnboardingCompleteResponse with generated plans (if any)
    """
    logger.info(f"Onboarding completion requested for user_id={user_id}")

    try:
        return complete_onboarding_flow(user_id=user_id, request=request)
    except Exception as e:
        logger.error(f"Error completing onboarding: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to complete onboarding: {e!s}") from e
