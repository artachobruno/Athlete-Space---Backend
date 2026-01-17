"""Token refresh helper for Strava OAuth tokens.

Provides utility functions to refresh access tokens using stored refresh tokens.
Tokens are encrypted at rest and decrypted only when needed for refresh.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.encryption import EncryptionError, EncryptionKeyError, decrypt_token, encrypt_token
from app.db.models import StravaAccount
from app.integrations.strava.tokens import refresh_access_token


class TokenRefreshError(Exception):
    """Raised when token refresh fails."""


def refresh_user_tokens(session: Session, user_id: str) -> tuple[str, int]:
    """Refresh access and refresh tokens for a user's Strava account.

    Decrypts stored refresh token, calls Strava API to refresh,
    encrypts new tokens, and updates database.

    Args:
        session: Database session
        user_id: User ID to refresh tokens for

    Returns:
        Tuple of (access_token, expires_at)

    Raises:
        TokenRefreshError: If refresh fails (e.g., invalid refresh token, network error)
    """
    logger.info(f"[TOKEN_REFRESH] Refreshing tokens for user_id={user_id}")

    # Get stored account
    account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

    if not account:
        raise TokenRefreshError(f"No Strava account found for user_id={user_id}")

    account_obj = account[0]

    # Decrypt refresh token
    try:
        refresh_token = decrypt_token(account_obj.refresh_token)
        logger.debug(f"[TOKEN_REFRESH] Decrypted refresh token for user_id={user_id}")
    except EncryptionKeyError as e:
        logger.error(f"[TOKEN_REFRESH] Encryption key mismatch for user_id={user_id}: {e}")
        raise TokenRefreshError("Failed to decrypt token: ENCRYPTION_KEY not set or changed. User must re-authenticate.") from e
    except EncryptionError as e:
        logger.error(f"[TOKEN_REFRESH] Failed to decrypt refresh token: {e}")
        raise TokenRefreshError(f"Failed to decrypt refresh token: {e}") from e

    # Call Strava API to refresh
    try:
        token_data = refresh_access_token(
            client_id=settings.strava_client_id,
            client_secret=settings.strava_client_secret,
            refresh_token=refresh_token,
        )
        logger.info(f"[TOKEN_REFRESH] Token refresh API call successful for user_id={user_id}")
    except Exception as e:
        logger.error(f"[TOKEN_REFRESH] Token refresh API call failed: {e}")
        raise TokenRefreshError(f"Failed to refresh token: {e}") from e

    # Extract new tokens
    new_access_token_raw = token_data["access_token"]
    new_refresh_token_raw = token_data.get("refresh_token") or refresh_token
    new_expires_at_raw = token_data["expires_at"]

    # Ensure tokens are strings
    if not isinstance(new_access_token_raw, str):
        raise TokenRefreshError(f"Invalid access_token type: {type(new_access_token_raw)}")
    if not isinstance(new_refresh_token_raw, str):
        raise TokenRefreshError(f"Invalid refresh_token type: {type(new_refresh_token_raw)}")

    # Ensure expires_at is an integer (epoch seconds)
    if not isinstance(new_expires_at_raw, int):
        raise TokenRefreshError(f"Invalid expires_at type: {type(new_expires_at_raw)}, expected int")

    new_access_token = new_access_token_raw
    new_refresh_token = new_refresh_token_raw

    # Convert epoch seconds to datetime (database expects TIMESTAMPTZ)
    expires_at_dt = datetime.fromtimestamp(new_expires_at_raw, tz=timezone.utc)

    # Encrypt new tokens
    try:
        encrypted_access_token = encrypt_token(new_access_token)
        encrypted_refresh_token = encrypt_token(new_refresh_token)
        logger.debug(f"[TOKEN_REFRESH] Encrypted new tokens for user_id={user_id}")
    except EncryptionError as e:
        logger.error(f"[TOKEN_REFRESH] Failed to encrypt new tokens: {e}")
        raise TokenRefreshError(f"Failed to encrypt new tokens: {e}") from e

    # Update database
    account_obj.access_token = encrypted_access_token
    account_obj.refresh_token = encrypted_refresh_token
    account_obj.expires_at = expires_at_dt
    session.commit()

    logger.info(f"[TOKEN_REFRESH] Tokens refreshed successfully for user_id={user_id}")
    return new_access_token, new_expires_at_raw
