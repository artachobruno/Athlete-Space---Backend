"""Profile API endpoints.

Provides endpoints for managing athlete profile and bio.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user_id
from app.db.models import AthleteBio
from app.db.session import get_session
from app.models.athlete_profile import AthleteProfile as AthleteProfileSchema
from app.services.athlete_profile_regeneration import handle_profile_change
from app.services.athlete_profile_regeneration import regenerate_bio as regenerate_bio_service
from app.services.athlete_profile_service import (
    get_profile_schema,
    update_structured_profile,
)

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileUpdateRequest(BaseModel):
    """Request for partial profile update."""

    identity: dict[str, Any] | None = Field(default=None, description="Identity information")
    goals: dict[str, Any] | None = Field(default=None, description="Goals information")
    constraints: dict[str, Any] | None = Field(default=None, description="Constraints information")
    training_context: dict[str, Any] | None = Field(default=None, description="Training context information")
    preferences: dict[str, Any] | None = Field(default=None, description="Preferences information")


class BioRegenerateResponse(BaseModel):
    """Response for bio regeneration."""

    success: bool = Field(description="Whether regeneration was successful")
    bio_text: str = Field(description="Generated bio text")
    confidence_score: float = Field(description="Confidence score (0.0-1.0)")
    source: str = Field(description="Bio source (ai_generated, user_edited, manual)")


class BioConfirmRequest(BaseModel):
    """Request to confirm/accept a bio."""

    bio_text: str | None = Field(default=None, description="Optional bio text to confirm (if None, confirms current)")


@router.get("", response_model=AthleteProfileSchema)
def get_profile(user_id: str = Depends(get_current_user_id)) -> AthleteProfileSchema:
    """Get athlete profile.

    Returns the complete athlete profile including structured data and narrative bio.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        AthleteProfileSchema with all profile data

    Raises:
        HTTPException: If profile retrieval fails
    """
    logger.info("GET /profile endpoint called", user_id=user_id)
    try:
        with get_session() as session:
            profile = get_profile_schema(session, user_id)
            logger.info("Profile retrieved successfully", user_id=user_id)
            return profile
    except Exception as e:
        logger.exception("Error getting profile", user_id=user_id, error=str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get profile: {e!s}") from e


@router.patch("", response_model=AthleteProfileSchema)
def update_profile(
    request: ProfileUpdateRequest,
    user_id: str = Depends(get_current_user_id),
) -> AthleteProfileSchema:
    """Update athlete profile with partial update.

    Only updates the fields provided in the request. All updates are partial merges.

    Args:
        request: Partial profile update
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Updated AthleteProfileSchema

    Raises:
        HTTPException: If profile update fails
    """
    logger.info("PATCH /profile endpoint called", user_id=user_id, updated_sections=list(request.model_dump(exclude_unset=True).keys()))

    def _track_changed_fields(partial_update: dict[str, Any]) -> list[str]:
        """Track changed fields for regeneration trigger."""
        changed_fields = []
        for section in ["identity", "goals", "constraints", "training_context", "preferences"]:
            if section in partial_update:
                section_data = partial_update[section]
                if isinstance(section_data, dict):
                    changed_fields.extend(f"{section}.{field_name}" for field_name in section_data)
        return changed_fields

    try:
        with get_session() as session:
            # Convert request to dict (only include provided fields)
            partial_update = request.model_dump(exclude_unset=True)

            # Track changed fields for regeneration trigger
            changed_fields = _track_changed_fields(partial_update)

            # Update profile
            update_structured_profile(session, user_id, partial_update)

            # Commit changes
            session.commit()

            # Handle bio regeneration if needed
            if changed_fields:
                try:
                    handle_profile_change(session, user_id, changed_fields)
                    session.commit()
                except Exception as e:
                    logger.warning("Bio regeneration failed (non-blocking)", user_id=user_id, error=str(e))
                    # Don't fail the request if bio regeneration fails

            # Return updated profile
            updated_profile = get_profile_schema(session, user_id)
            logger.info("Profile updated successfully", user_id=user_id)
            return updated_profile

    except Exception as e:
        logger.exception("Error updating profile", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update profile: {e!s}",
        ) from e


@router.post("/bio/regenerate", response_model=BioRegenerateResponse)
def regenerate_bio(user_id: str = Depends(get_current_user_id)) -> BioRegenerateResponse:
    """Regenerate athlete bio.

    Force regeneration of the bio from current profile data.
    Only works if bio source is 'ai_generated' or no bio exists.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        BioRegenerateResponse with regenerated bio

    Raises:
        HTTPException: If bio regeneration fails
    """
    logger.info("POST /profile/bio/regenerate endpoint called", user_id=user_id)

    def _raise_bio_not_created() -> None:
        """Raise error for bio not created."""
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Bio regeneration failed - no bio created",
        )

    try:
        with get_session() as session:
            # Regenerate bio
            regenerate_bio_service(session, user_id)
            session.commit()

            # Get updated bio
            bio = session.query(AthleteBio).filter_by(user_id=user_id).order_by(AthleteBio.created_at.desc()).first()

            if not bio:
                _raise_bio_not_created()

            logger.info("Bio regenerated successfully", user_id=user_id, bio_id=bio.id)
            return BioRegenerateResponse(
                success=True,
                bio_text=bio.text,
                confidence_score=bio.confidence_score,
                source=bio.source,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error regenerating bio", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to regenerate bio: {e!s}",
        ) from e


@router.post("/bio/confirm", response_model=BioRegenerateResponse)
def confirm_bio(
    request: BioConfirmRequest,
    user_id: str = Depends(get_current_user_id),
) -> BioRegenerateResponse:
    """Confirm/accept a bio.

    If bio_text is provided, updates the bio with that text and marks as 'user_edited'.
    If bio_text is None, marks current bio as confirmed (clears stale flag).

    Args:
        request: Bio confirmation request
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        BioRegenerateResponse with confirmed bio

    Raises:
        HTTPException: If bio confirmation fails
    """
    logger.info("POST /profile/bio/confirm endpoint called", user_id=user_id)

    def _raise_bio_not_found() -> None:
        """Raise error for bio not found."""
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No bio found to confirm",
        )

    try:
        with get_session() as session:
            # Get or create bio
            bio = session.query(AthleteBio).filter_by(user_id=user_id).order_by(AthleteBio.created_at.desc()).first()

            if request.bio_text:
                # Update bio with provided text
                if bio:
                    bio.text = request.bio_text
                    bio.source = "user_edited"
                    bio.stale = False
                    bio.updated_at = datetime.now(timezone.utc)
                else:
                    # Create new bio
                    bio = AthleteBio(
                        id=str(uuid4()),
                        user_id=user_id,
                        text=request.bio_text,
                        confidence_score=1.0,  # User-edited = full confidence
                        source="user_edited",
                        depends_on_hash=None,
                        last_generated_at=None,
                        stale=False,
                    )
                    session.add(bio)
            else:
                # Just confirm current bio (clear stale flag)
                if not bio:
                    _raise_bio_not_found()
                bio.stale = False

            session.commit()

            logger.info("Bio confirmed successfully", user_id=user_id, bio_id=bio.id)
            return BioRegenerateResponse(
                success=True,
                bio_text=bio.text,
                confidence_score=bio.confidence_score,
                source=bio.source,
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error confirming bio", user_id=user_id, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to confirm bio: {e!s}",
        ) from e
