"""FastAPI authentication dependency for JWT-based auth.

Provides get_current_user_id dependency that extracts and verifies JWT tokens
from the Authorization header.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from loguru import logger

from app.core.auth_jwt import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def get_current_user_id(request: Request, token: str | None = Depends(oauth2_scheme)) -> str:
    """FastAPI dependency to get current authenticated user ID from JWT token.

    Extracts JWT token from Authorization header, verifies it, and returns user_id.

    Args:
        request: FastAPI request object (for logging)
        token: JWT token (automatically extracted from Authorization header by FastAPI)

    Returns:
        User ID (string) from token

    Raises:
        HTTPException: 401 if token is missing, invalid, or expired
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
        return decode_access_token(token)
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
