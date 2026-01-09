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


def _get_auth_token(request: Request, token: str | None = Depends(oauth2_scheme)) -> str | None:
    """Extract auth token from either Authorization header or cookie.

    React-compatible dual-mode authentication:
    - Mobile: Token in Authorization header (Bearer token)
    - Web: Token in cookie (session)

    Args:
        request: FastAPI request object
        token: JWT token from Authorization header (if present)

    Returns:
        Token string if found, None otherwise
    """
    # Try Authorization header first (mobile/API clients)
    if token:
        return token

    # Try cookie (web)
    session_token = request.cookies.get("session")
    if session_token:
        return session_token

    return None


def get_current_user_id(request: Request, token: str | None = Depends(oauth2_scheme)) -> str:
    """FastAPI dependency to get current authenticated user ID from JWT token.

    Supports dual-mode authentication (React-compatible):
    - Mobile: Token in Authorization header (Bearer token)
    - Web: Token in cookie (session)

    Extracts JWT token from Authorization header or cookie, verifies it,
    checks if user is active, and returns user_id.

    Args:
        request: FastAPI request object (for logging)
        token: JWT token (automatically extracted from Authorization header by FastAPI)

    Returns:
        User ID (string) from token

    Raises:
        HTTPException: 401 if token is missing, invalid, or expired
        HTTPException: 403 if user account is inactive
    """
    # Get token from header or cookie
    auth_token = _get_auth_token(request, token)

    if not auth_token:
        auth_header = request.headers.get("Authorization")
        cookie_present = "session" in request.cookies

        # Log all headers for debugging (but mask sensitive values)
        all_headers = dict(request.headers)
        sensitive_headers = {"authorization", "cookie"}
        headers_log = {k: (v[:20] + "..." if len(v) > 20 else v) if k.lower() in sensitive_headers else v for k, v in all_headers.items()}

        logger.warning(
            f"Auth failed: Missing authentication token. "
            f"Authorization header: {auth_header[:50] if auth_header else 'None'}, "
            f"Cookie present: {cookie_present}, "
            f"Path: {request.url.path}, Method: {request.method}, "
            f"Origin: {request.headers.get('Origin', 'None')}"
        )

        # Log full headers at debug level for detailed troubleshooting
        logger.debug(f"Full request headers received for {request.method} {request.url.path}:\n  {dict(headers_log)}")

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please provide a valid Bearer token in the Authorization header or a session cookie.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = decode_access_token(auth_token)
    except ValueError as e:
        logger.warning(
            f"Auth failed: {e}, Path: {request.url.path}, Method: {request.method}, Token preview: {auth_token[:20]}..."
            if len(auth_token) > 20
            else f"Token: {auth_token}"
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

    Supports dual-mode authentication (React-compatible):
    - Mobile: Token in Authorization header (Bearer token)
    - Web: Token in cookie (session)

    Args:
        request: FastAPI request object (for logging)
        token: JWT token (automatically extracted from Authorization header by FastAPI)

    Returns:
        User ID (string) from token if authenticated, None otherwise
    """
    # Get token from header or cookie
    auth_token = _get_auth_token(request, token)
    if not auth_token:
        return None

    try:
        user_id = decode_access_token(auth_token)
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
