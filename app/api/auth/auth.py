"""Authentication endpoints for JWT-based auth.

Provides:
- Email/password signup and login
- Account linking for OAuth users
- Legacy Strava athlete_id login (for backward compatibility)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.core.auth_jwt import create_access_token
from app.core.password import hash_password, verify_password
from app.db.models import User
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    """Signup request model."""

    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        """Validate password strength."""
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return value


class LoginRequest(BaseModel):
    """Email/password login request model."""

    email: EmailStr
    password: str


def _normalize_email(email: str) -> str:
    """Normalize email to lowercase."""
    return email.lower().strip()


@router.post("/signup")
def signup(request: SignupRequest):
    """Sign up with email and password.

    Email and password are mandatory. There is no anonymous account path.

    Args:
        request: Signup request containing email and password

    Returns:
        JWT token and user information

    Raises:
        HTTPException: 409 if email already exists, 400 if invalid input
    """
    # Validate that email and password are provided (Pydantic handles this, but explicit check)
    if not request.email or not request.password:
        logger.warning("[AUTH] Signup failed: email or password missing")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_credentials", "message": "Email and password are required"},
        )

    normalized_email = _normalize_email(request.email)
    logger.info(f"[AUTH] Signup requested for email={normalized_email}")

    with get_session() as session:
        # Check if email already exists
        existing_user = session.execute(select(User).where(User.email == normalized_email)).first()

        if existing_user:
            logger.warning(f"[AUTH] Signup failed: email already exists={normalized_email}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "email_already_exists", "message": "An account with this email already exists"},
            )

        # Hash password
        password_hash = hash_password(request.password)

        # Create user with UUID-based ID
        user_id = str(uuid.uuid4())
        new_user = User(
            id=user_id,
            email=normalized_email,
            password_hash=password_hash,
            strava_athlete_id=None,
            created_at=datetime.now(timezone.utc),
            last_login_at=None,
        )

        session.add(new_user)
        session.commit()

        logger.info(f"[AUTH] User created: user_id={user_id}, email={normalized_email}")

        # Update last_login_at
        new_user.last_login_at = datetime.now(timezone.utc)
        session.commit()

        # Create JWT token
        token = create_access_token(user_id)

        logger.info(f"[AUTH] Signup successful for user_id={user_id}, email={normalized_email}")

        return {
            "access_token": token,
            "token_type": "bearer",
            "user_id": user_id,
            "email": normalized_email,
        }


@router.post("/login")
def login(request: LoginRequest):
    """Login with email and password.

    Args:
        request: Login request containing email and password

    Returns:
        JWT token and user information

    Raises:
        HTTPException: 404 if user not found, 401 if password is incorrect
    """
    normalized_email = _normalize_email(request.email)
    logger.info(f"[AUTH] Login requested for email={normalized_email}")

    with get_session() as session:
        # Find user by email
        user_result = session.execute(select(User).where(User.email == normalized_email)).first()

        if not user_result:
            logger.warning(f"[AUTH] Login failed: user not found for email={normalized_email}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "user_not_found", "message": "No account found with this email"},
            )

        user = user_result[0]

        # Check if user is active
        if not user.is_active:
            logger.warning(f"[AUTH] Login failed: inactive user for email={normalized_email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account inactive. Please sign up again.",
            )

        # Check if user has a password
        if not user.password_hash:
            logger.warning(f"[AUTH] Login failed: user has no password for email={normalized_email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "invalid_credentials", "message": "Invalid email or password"},
            )

        # Verify password
        if not verify_password(request.password, user.password_hash):
            logger.warning(f"[AUTH] Login failed: invalid password for email={normalized_email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "invalid_credentials", "message": "Invalid email or password"},
            )

        # Update last_login_at
        user.last_login_at = datetime.now(timezone.utc)
        session.commit()

        # Create JWT token
        token = create_access_token(user.id)

        logger.info(f"[AUTH] Login successful for user_id={user.id}, email={normalized_email}")

        return {
            "access_token": token,
            "token_type": "bearer",
            "user_id": user.id,
            "email": user.email,
        }
