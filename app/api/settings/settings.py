"""Settings API endpoints.

Provides endpoints for managing user settings including profile information.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.api.schemas.athlete_profile import AthleteProfileUpsert
from app.api.schemas.schemas import SettingsProfileResponse, SettingsProfileUpdateRequest
from app.db.models import User
from app.db.session import get_session
from app.users.profile_service import upsert_athlete_profile

router = APIRouter(prefix="/settings", tags=["settings"])


def _raise_user_not_found(user_id: str) -> None:
    """Raise HTTPException for user not found."""
    logger.error(f"[API] User not found for user_id={user_id}")
    raise HTTPException(status_code=404, detail="User not found")


def _raise_invalid_role(role: str) -> None:
    """Raise HTTPException for invalid role."""
    raise HTTPException(
        status_code=400,
        detail=f"Invalid role: {role}. Must be 'athlete' or 'coach'",
    )


@router.get("/profile", response_model=SettingsProfileResponse)
def get_settings_profile(user_id: str = Depends(get_current_user_id)):
    """Get user profile settings.

    Returns email, name, and role for the current user.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        SettingsProfileResponse with email, first_name, last_name, and role
    """
    logger.info(f"[API] GET /settings/profile endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                _raise_user_not_found(user_id)

            user = user_result[0]
            session.expunge(user)

            return SettingsProfileResponse(
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                role=user.role,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting settings profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get settings profile: {e!s}") from e


@router.put("/profile")
def update_settings_profile(
    request: AthleteProfileUpsert,
    user_id: str = Depends(get_current_user_id),
):
    """Update user profile settings.

    Uses the same schema and logic as onboarding completion.
    This ensures consistency between onboarding and settings updates.

    Args:
        request: Profile data to update (same schema as onboarding)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success response
    """
    logger.info(f"[API] PUT /settings/profile endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            # Use shared service to upsert profile data
            upsert_athlete_profile(
                user_id=user_id,
                payload=request,
                session=session,
            )

            logger.info(f"[API] Settings profile updated for user_id={user_id}")
            return {"status": "ok"}
    except ValueError as e:
        logger.error(f"[API] User not found for user_id={user_id}: {e}")
        raise HTTPException(status_code=404, detail="User not found") from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating settings profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update settings profile: {e!s}") from e


@router.patch("/profile", response_model=SettingsProfileResponse)
def update_settings_profile_legacy(
    request: SettingsProfileUpdateRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Update user profile settings (legacy endpoint for backward compatibility).

    Updates first_name, last_name, and/or role for the current user.
    Role can be changed, but a warning should be shown in the frontend.

    Args:
        request: Settings profile update request
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated SettingsProfileResponse
    """
    logger.info(f"[API] PATCH /settings/profile endpoint called for user_id={user_id}")
    try:
        with get_session() as session:
            user_result = session.execute(select(User).where(User.id == user_id)).first()
            if not user_result:
                _raise_user_not_found(user_id)

            user = user_result[0]

            # Update fields if provided
            if request.first_name is not None:
                user.first_name = request.first_name
            if request.last_name is not None:
                user.last_name = request.last_name
            if request.role is not None:
                # Validate role value
                if request.role not in {"athlete", "coach"}:
                    _raise_invalid_role(request.role)
                user.role = request.role

            session.commit()
            session.refresh(user)
            session.expunge(user)

            logger.info(f"[API] Settings profile updated for user_id={user_id}")
            return SettingsProfileResponse(
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
                role=user.role,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating settings profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update settings profile: {e!s}") from e
