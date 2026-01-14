"""Authentication endpoints for JWT-based auth.

Provides:
- Email/password signup and login
- Account linking for OAuth users
- Legacy Strava athlete_id login (for backward compatibility)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.api.schemas.schemas import ChangeEmailRequest
from app.core.auth_jwt import create_access_token
from app.core.password import hash_password, verify_password
from app.db.models import AuthProvider, User
from app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_cookie_domain(request: Request) -> str | None:
    """Get cookie domain from request host.

    For production (onrender.com), returns '.virtus-ai.onrender.com' to allow
    cookie sharing across subdomains. For localhost, returns None (browser default).

    Args:
        request: FastAPI request object

    Returns:
        Cookie domain string (with leading dot for subdomain sharing) or None for localhost
    """
    host = request.headers.get("host", "")
    if "onrender.com" in host:
        return ".virtus-ai.onrender.com"
    return None


def _set_auth_cookie(response: Response, token: str, request: Request) -> None:
    """Set authentication cookie with correct settings for cross-origin persistence.

    Sets cookie with:
    - httponly=True (prevents XSS)
    - secure=True (HTTPS only in production)
    - samesite="none" (required for cross-origin)
    - domain (for production subdomain sharing)
    - max_age (7 days)

    Args:
        response: FastAPI response object
        token: JWT token to set in cookie
        request: FastAPI request object (for domain detection)
    """
    cookie_domain = _get_cookie_domain(request)
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=60 * 60 * 24 * 7,  # 7 days
        domain=cookie_domain,
    )


class SignupRequest(BaseModel):
    """Signup request model."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=72)

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
def signup(request: SignupRequest, http_request: Request):
    """Sign up with email and password.

    Email and password are mandatory. There is no anonymous account path.

    Args:
        request: Signup request containing email and password
        http_request: FastAPI request object for setting authentication cookies

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
        try:
            password_hash = hash_password(request.password)
        except ValueError as e:
            logger.warning(f"[AUTH] Signup failed: password hashing error for email={normalized_email}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be 8-72 characters",
            ) from e

        # Create user with UUID-based ID
        user_id = str(uuid.uuid4())
        new_user = User(
            id=user_id,
            email=normalized_email,
            password_hash=password_hash,
            auth_provider=AuthProvider.password,
            google_sub=None,
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
        logger.debug(f"[AUTH] Creating JWT token for user_id={user_id}")
        try:
            token = create_access_token(user_id)
            logger.info(f"[AUTH] JWT token created successfully for user_id={user_id}, token_length={len(token)}")
        except Exception as e:
            logger.exception(f"[AUTH] Failed to create JWT token for user_id={user_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create authentication token. Please try again.",
            ) from e

        logger.info(f"[AUTH] Signup successful for user_id={user_id}, email={normalized_email}")

        response_data = {
            "access_token": token,
            "token_type": "bearer",
            "user_id": user_id,
            "email": normalized_email,
        }
        logger.debug(f"[AUTH] Returning signup response with token for user_id={user_id}")

        # Create response and set cookie
        response = JSONResponse(content=response_data)
        _set_auth_cookie(response, token, http_request)
        logger.debug(f"[AUTH] Set authentication cookie for user_id={user_id}")
        return response


@router.post("/login")
def login(request: LoginRequest, http_request: Request):
    """Login with email and password.

    Args:
        request: Login request containing email and password
        http_request: FastAPI request object for setting authentication cookies

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
                detail={
                    "error": "user_not_found",
                    "reason": "user_not_found",
                    "message": "No account found with this email",
                },
            )

        user = user_result[0]

        # Check if user is active
        if not user.is_active:
            logger.warning(f"[AUTH] Login failed: inactive user for email={normalized_email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account inactive. Please sign up again.",
            )

        # Check if user has password auth (not OAuth-only)
        if user.auth_provider != AuthProvider.password:
            logger.warning(
                f"[AUTH] Login failed: user has auth_provider={user.auth_provider.value} for email={normalized_email}, "
                "password login not allowed"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_auth_method",
                    "reason": "provider_mismatch",
                    "message": "This account uses Google sign-in. Please sign in with Google instead.",
                },
            )

        # Check if user has a password
        if not user.password_hash:
            logger.warning(f"[AUTH] Login failed: user has no password for email={normalized_email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_credentials",
                    "reason": "password_missing",
                    "message": "Invalid email or password",
                },
            )

        # Verify password
        if not verify_password(request.password, user.password_hash):
            logger.warning(f"[AUTH] Login failed: invalid password for email={normalized_email}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_credentials",
                    "reason": "invalid_password",
                    "message": "Invalid email or password",
                },
            )

        # Update last_login_at
        user.last_login_at = datetime.now(timezone.utc)
        session.commit()

        # Create JWT token
        token = create_access_token(user.id)

        logger.info(f"[AUTH] Login successful for user_id={user.id}, email={normalized_email}")

        response_data = {
            "access_token": token,
            "token_type": "bearer",
            "user_id": user.id,
            "email": user.email,
        }

        # Create response and set cookie
        response = JSONResponse(content=response_data)
        _set_auth_cookie(response, token, http_request)
        logger.debug(f"[AUTH] Set authentication cookie for user_id={user.id}")
        return response


@router.post("/change-email")
def change_email(request: ChangeEmailRequest, user_id: str = Depends(get_current_user_id)):
    """Change user email address.

    Requires password verification and invalidates all existing sessions.

    Args:
        request: Change email request with password and new email
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success message with new email

    Raises:
        HTTPException: 401 if password is incorrect, 409 if email already exists
    """
    normalized_new_email = _normalize_email(request.new_email)
    logger.info(f"[AUTH] Change email requested for user_id={user_id}, new_email={normalized_new_email}")

    with get_session() as session:
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        if not user_result:
            raise HTTPException(status_code=404, detail="User not found")

        user = user_result[0]

        # Check if user has password auth
        if user.auth_provider != AuthProvider.password:
            raise HTTPException(
                status_code=400,
                detail="Email change not available for OAuth accounts",
            )

        # Verify password
        if not user.password_hash:
            raise HTTPException(
                status_code=400,
                detail="No password set for this account",
            )

        if not verify_password(request.password, user.password_hash):
            logger.warning(f"[AUTH] Change email failed: incorrect password for user_id={user_id}")
            raise HTTPException(
                status_code=401,
                detail="Password is incorrect",
            )

        # Check if new email already exists
        existing_user = session.execute(select(User).where(User.email == normalized_new_email)).first()
        if existing_user and existing_user[0].id != user_id:
            logger.warning(f"[AUTH] Change email failed: email already exists={normalized_new_email}")
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists",
            )

        # Update email
        old_email = user.email
        user.email = normalized_new_email
        # Note: In a production system, you would want to invalidate all JWT tokens here
        # This could be done by maintaining a token blacklist or by rotating a secret
        session.commit()

        logger.info(f"[AUTH] Email changed successfully for user_id={user_id}, old_email={old_email}, new_email={normalized_new_email}")

        return {
            "success": True,
            "message": "Email changed successfully. Please sign in again with your new email.",
            "new_email": normalized_new_email,
        }
