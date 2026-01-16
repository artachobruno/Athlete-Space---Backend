"""JWT token creation and verification utilities.

Handles stateless JWT tokens issued by the backend for user authentication.
Tokens are based on Strava user authentication and contain user_id in the 'sub' claim.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from loguru import logger

from app.config.settings import settings


def create_access_token(user_id: str) -> str:
    """Create a JWT access token for a user.

    Args:
        user_id: User ID (string or UUID) to encode in token

    Returns:
        JWT token string
    """
    # Ensure user_id is a string (handle UUID objects from database)
    user_id_str = str(user_id) if user_id is not None else ""
    if not user_id_str:
        raise ValueError("user_id cannot be None or empty")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id_str,
        "exp": now + timedelta(days=settings.auth_token_expire_days),
        "iat": now,
        "iss": "virtus-backend",
    }
    return jwt.encode(
        payload,
        settings.auth_secret_key,
        algorithm=settings.auth_algorithm,
    )


def decode_access_token(token: str) -> str:
    """Decode and verify a JWT access token.

    Args:
        token: JWT token string

    Returns:
        User ID (string) from token 'sub' claim

    Raises:
        ValueError: If token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.auth_secret_key,
            algorithms=[settings.auth_algorithm],
        )
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("Token missing user ID")
        return str(user_id)
    except JWTError as e:
        logger.warning(f"JWT decode failed: {e}")
        raise ValueError("Invalid or expired token") from e
