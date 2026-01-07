"""Onboarding API routes.

HTTP boundary for onboarding endpoints. Contains only FastAPI routing logic.
All business logic lives in app.onboarding.service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.api.dependencies.auth import get_current_user_id
from app.onboarding.schemas import OnboardingCompleteRequest, OnboardingCompleteResponse
from app.onboarding.service import complete_onboarding_flow

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


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
