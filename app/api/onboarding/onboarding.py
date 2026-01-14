"""Onboarding API routes.

HTTP boundary for onboarding endpoints. Contains only FastAPI routing logic.
All business logic lives in app.onboarding.service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import func, select

from app.api.dependencies.auth import get_current_user_id
from app.db.models import Activity, StravaAccount, User
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
    1. User has credentials (email and password)
    2. Strava is connected
    3. Activity data has been synced from Strava
    4. Onboarding can proceed (has data to populate)

    Args:
        user_id: Current authenticated user ID

    Returns:
        Dictionary with:
        - has_credentials: bool - Whether user has email and password
        - connected: bool - Whether Strava is connected
        - has_data: bool - Whether activity data exists
        - activity_count: int - Number of activities synced
        - ready: bool - Whether onboarding can proceed (has credentials and data)
    """
    logger.info(f"Onboarding status check for user_id={user_id}")

    with get_session() as session:
        # Verify user has credentials
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        has_credentials = False
        if user_result:
            user = user_result[0]
            has_credentials = bool(user.email and user.password_hash)

        if not has_credentials:
            return {
                "has_credentials": False,
                "connected": False,
                "has_data": False,
                "activity_count": 0,
                "ready": False,
            }

        # Check if Strava is connected
        account_result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        connected = account_result is not None

        if not connected:
            return {
                "has_credentials": True,
                "connected": False,
                "has_data": False,
                "activity_count": 0,
                "ready": False,
            }

        # Check if activity data exists
        activity_count_result = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar()
        activity_count = activity_count_result if activity_count_result is not None else 0
        has_data = activity_count > 0

        logger.info(
            f"Onboarding status for user_id={user_id}: "
            f"has_credentials={has_credentials}, connected={connected}, "
            f"has_data={has_data}, activity_count={activity_count}"
        )

        return {
            "has_credentials": True,
            "connected": True,
            "has_data": has_data,
            "activity_count": activity_count,
            "ready": has_data,  # Ready when we have credentials and data to populate
        }


@router.post("/complete", response_model=OnboardingCompleteResponse)
def complete_onboarding(
    request: OnboardingCompleteRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Complete onboarding process.

    This endpoint:
    1. Persists onboarding data to users, athlete_profiles, and user_settings
    2. Sets onboarding_complete flag
    3. Conditionally generates plans (if user opted in and has data)

    Args:
        request: Onboarding completion request with all required fields
        user_id: Current authenticated user ID

    Returns:
        OnboardingCompleteResponse with generated plans (if any)

    Raises:
        HTTPException: 404 if user not found, 422 if validation fails, 500 on other errors
    """
    logger.info(f"Onboarding completion requested for user_id={user_id}")

    # Verify user exists
    with get_session() as session:
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        if not user_result:
            logger.exception(f"Onboarding failed: user not found user_id={user_id}")
            raise HTTPException(status_code=404, detail="User not found")

    try:
        return complete_onboarding_flow(user_id=user_id, request=request)
    except ValueError as e:
        # User not found or validation error
        logger.exception(f"Validation error completing onboarding: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid request: {e!s}") from e
    except Exception as e:
        logger.exception(f"Error completing onboarding: {e}")
        # Include error type and message for better debugging
        error_detail = f"{type(e).__name__}: {e!s}" if str(e) else f"{type(e).__name__}"
        raise HTTPException(status_code=500, detail=f"Failed to complete onboarding: {error_detail}") from e
