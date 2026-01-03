"""Authentication middleware and user context management.

Handles JWT verification from Clerk and provides get_current_user dependency
for FastAPI endpoints. Supports dev mode override via DEV_USER_ID.
"""

from __future__ import annotations

from typing import Annotated, NoReturn

import jwt
from fastapi import Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy import select

from app.core.settings import settings
from app.state.db import get_session
from app.state.models import User


def _raise_unauthorized(detail: str = "Authentication required") -> NoReturn:
    """Raise HTTPException for unauthorized requests."""
    logger.warning(f"Unauthorized request: {detail}")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _verify_clerk_jwt(token: str) -> str:
    """Verify Clerk JWT token and extract user ID.

    Args:
        token: JWT token string

    Returns:
        User ID (Clerk user ID) as string

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        # For Clerk, we need to verify the JWT using their public key
        # In production, fetch the public key from Clerk's JWKS endpoint
        # For now, we'll decode without verification in dev mode if no secret key is set
        if not settings.clerk_secret_key:
            logger.warning("CLERK_SECRET_KEY not set - decoding token without verification (dev mode)")
            decoded = jwt.decode(token, options={"verify_signature": False})
        else:
            # In production, verify with Clerk's secret key
            decoded = jwt.decode(token, settings.clerk_secret_key, algorithms=["HS256"])

        user_id = decoded.get("sub") or decoded.get("user_id")
        if not user_id:
            _raise_unauthorized("Token missing user ID")

        return str(user_id)
    except jwt.ExpiredSignatureError:
        _raise_unauthorized("Token expired")
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        _raise_unauthorized("Invalid token")


def _get_user_id_from_request(request: Request) -> str:
    """Extract user ID from request (token or dev override).

    Args:
        request: FastAPI request object

    Returns:
        User ID as string

    Raises:
        HTTPException: If authentication fails
    """
    # Dev mode override: check DEV_USER_ID environment variable
    if settings.dev_user_id:
        logger.debug(f"Using dev mode user override: {settings.dev_user_id}")
        return settings.dev_user_id

    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        _raise_unauthorized("Missing Authorization header")

    if not auth_header.startswith("Bearer "):
        _raise_unauthorized("Invalid Authorization header format")

    token = auth_header.replace("Bearer ", "").strip()
    if not token:
        _raise_unauthorized("Missing token")

    # Verify token and extract user ID
    return _verify_clerk_jwt(token)


def _get_or_create_user(user_id: str) -> User:
    """Get or create user in database.

    Args:
        user_id: User ID from Clerk (string)

    Returns:
        User database record

    Raises:
        HTTPException: If user creation fails
    """
    with get_session() as session:
        # Try to get existing user
        result = session.execute(select(User).where(User.id == user_id)).first()
        if result:
            return result[0]

        # Create new user (idempotent - email will be None initially)
        try:
            new_user = User(id=user_id, email=None)
            session.add(new_user)
            session.commit()
            logger.info(f"Created new user: {user_id}")
        except Exception as e:
            logger.error(f"Failed to create user {user_id}: {e}")
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create user: {e!s}",
            ) from e
        else:
            return new_user


def get_current_user(request: Annotated[Request, Depends()]) -> str:
    """FastAPI dependency to get current authenticated user ID.

    This function:
    1. Extracts JWT token from Authorization header
    2. Verifies token with Clerk
    3. Gets or creates user in database
    4. Returns user_id as string

    Args:
        request: FastAPI request object (injected by Depends)

    Returns:
        User ID as string (UUID format)

    Raises:
        HTTPException: 401 if authentication fails
    """
    # Extract and verify user ID from token
    clerk_user_id = _get_user_id_from_request(request)

    # Get or create user in database
    user = _get_or_create_user(clerk_user_id)

    return user.id
