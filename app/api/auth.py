"""Authentication endpoints for JWT-based auth.

Provides login endpoint that issues JWT tokens based on Strava athlete_id.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth_jwt import create_access_token
from app.state.db import get_session
from app.state.models import StravaAccount, User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Login request model."""

    athlete_id: int


@router.post("/login")
def login_with_strava(request: LoginRequest):
    """Login with Strava athlete_id and get JWT token.

    Args:
        request: Login request containing athlete_id

    Returns:
        JWT token and user information

    Raises:
        HTTPException: 404 if user not found
    """
    logger.info(f"[AUTH] Login requested for athlete_id={request.athlete_id}")

    with get_session() as session:
        # Find StravaAccount by athlete_id
        account_result = session.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(request.athlete_id))).first()

        if not account_result:
            logger.warning(f"[AUTH] No Strava account found for athlete_id={request.athlete_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found. Please connect your Strava account first.",
            )

        account = account_result[0]
        user_id = account.user_id

        # Verify user exists
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        if not user_result:
            logger.warning(f"[AUTH] User {user_id} not found in users table")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Create JWT token
        token = create_access_token(user_id)

        logger.info(f"[AUTH] Login successful for user_id={user_id}, athlete_id={request.athlete_id}")

        return {
            "access_token": token,
            "token_type": "bearer",
            "user_id": user_id,
        }
