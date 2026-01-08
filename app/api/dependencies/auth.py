"""FastAPI authentication dependency for JWT-based auth.

Provides get_current_user_id dependency that extracts and verifies JWT tokens
from the Authorization header and checks if the user is active.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from loguru import logger
from sqlalchemy import select

from app.core.auth_jwt import decode_access_token
from app.db.models import User
from app.db.session import get_session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def get_current_user_id(request: Request, token: str | None = Depends(oauth2_scheme)) -> str:
    """FastAPI dependency to get current authenticated user ID from JWT token.

    Extracts JWT token from Authorization header, verifies it, checks if user is active,
    and returns user_id.

    Args:
        request: FastAPI request object (for logging)
        token: JWT token (automatically extracted from Authorization header by FastAPI)

    Returns:
        User ID (string) from token

    Raises:
        HTTPException: 401 if token is missing, invalid, or expired
        HTTPException: 403 if user account is inactive
    """
    if not token:
        auth_header = request.headers.get("Authorization")
        # Log all headers for debugging (but mask sensitive values)
        all_headers = dict(request.headers)
        sensitive_headers = {"authorization", "cookie"}
        headers_log = {k: (v[:20] + "..." if len(v) > 20 else v) if k.lower() in sensitive_headers else v for k, v in all_headers.items()}

        logger.warning(
            f"Auth failed: Missing or invalid Authorization header. "
            f"Header value: {auth_header[:50] if auth_header else 'None'}, "
            f"Path: {request.url.path}, Method: {request.method}, "
            f"Origin: {request.headers.get('Origin', 'None')}, "
            f"All headers: {list(headers_log.keys())}"
        )

        # Log full headers at debug level for detailed troubleshooting
        logger.debug(f"Full request headers received for {request.method} {request.url.path}:\n  {dict(headers_log)}")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please provide a valid Bearer token in the Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = decode_access_token(token)
    except ValueError as e:
        logger.warning(
            f"Auth failed: {e}, Path: {request.url.path}, Method: {request.method}, Token preview: {token[:20]}..."
            if len(token) > 20
            else f"Token: {token}"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    # Check if user is active
    with get_session() as session:
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        if not user_result:
            logger.warning(f"Auth failed: User not found user_id={user_id}, Path: {request.url.path}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user = user_result[0]
        if not user.is_active:
            logger.warning(f"Auth failed: Inactive user user_id={user_id}, Path: {request.url.path}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account inactive. Please sign up again.",
            )

    return user_id


def get_optional_user_id(request: Request, token: str | None = Depends(oauth2_scheme)) -> str | None:
    """FastAPI dependency to get current authenticated user ID from JWT token (optional).

    Similar to get_current_user_id, but returns None if token is missing or invalid
    instead of raising an exception. Useful for endpoints that support both
    authenticated and unauthenticated access.

    Args:
        request: FastAPI request object (for logging)
        token: JWT token (automatically extracted from Authorization header by FastAPI)

    Returns:
        User ID (string) from token if authenticated, None otherwise
    """
    if not token:
        return None

    try:
        user_id = decode_access_token(token)
    except ValueError:
        logger.debug(f"Optional auth: Invalid token for path={request.url.path}")
        return None

    # Check if user is active
    with get_session() as session:
        user_result = session.execute(select(User).where(User.id == user_id)).first()
        if not user_result:
            logger.debug(f"Optional auth: User not found user_id={user_id}, Path: {request.url.path}")
            return None

        user = user_result[0]
        if not user.is_active:
            logger.debug(f"Optional auth: Inactive user user_id={user_id}, Path: {request.url.path}")
            return None

    return user_id
