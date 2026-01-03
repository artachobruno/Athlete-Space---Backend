"""FastAPI authentication dependency for JWT-based auth.

Provides get_current_user_id dependency that extracts and verifies JWT tokens
from the Authorization header.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from loguru import logger

from app.core.auth_jwt import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


def get_current_user_id(token: str = Depends(oauth2_scheme)) -> str:
    """FastAPI dependency to get current authenticated user ID from JWT token.

    Extracts JWT token from Authorization header, verifies it, and returns user_id.

    Args:
        token: JWT token (automatically extracted from Authorization header by FastAPI)

    Returns:
        User ID (string) from token

    Raises:
        HTTPException: 401 if token is missing, invalid, or expired
    """
    try:
        return decode_access_token(token)
    except ValueError as e:
        logger.warning(f"Auth failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
