"""Garmin token refresh service.

Handles automatic token refresh on expiry or 401 errors.
Never lets sync jobs crash due to expired tokens.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.encryption import EncryptionError, EncryptionKeyError, decrypt_token, encrypt_token
from app.db.models import UserIntegration
from app.db.session import get_session
from app.integrations.garmin.oauth import refresh_access_token


class GarminTokenRefreshError(Exception):
    """Raised when Garmin token refresh fails."""


def refresh_garmin_tokens(user_id: str, buffer_seconds: int = 300) -> tuple[str, datetime]:
    """Refresh Garmin access token if expired or near expiry.

    Args:
        user_id: User ID
        buffer_seconds: Refresh if token expires within this many seconds (default: 5 min)

    Returns:
        Tuple of (access_token, expires_at)

    Raises:
        GarminTokenRefreshError: If refresh fails or integration not found
    """
    logger.info(f"[GARMIN_TOKEN] Refreshing tokens for user_id={user_id}")

    with get_session() as session:
        integration = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "garmin",
                UserIntegration.revoked_at.is_(None),
            )
        ).first()

        if not integration:
            raise GarminTokenRefreshError(f"No active Garmin integration for user_id={user_id}")

        integration_obj = integration[0]

        # Decrypt refresh token
        try:
            refresh_token = decrypt_token(integration_obj.refresh_token)
        except EncryptionKeyError as e:
            logger.error(f"[GARMIN_TOKEN] Encryption key mismatch for user_id={user_id}: {e}")
            raise GarminTokenRefreshError("Failed to decrypt token: ENCRYPTION_KEY not set or changed. User must re-authenticate.") from e
        except EncryptionError as e:
            logger.error(f"[GARMIN_TOKEN] Failed to decrypt refresh token for user_id={user_id}: {e}")
            raise GarminTokenRefreshError(f"Failed to decrypt refresh token: {e}") from e

        # Check if refresh is needed
        now = datetime.now(timezone.utc)
        needs_refresh = False

        if not integration_obj.token_expires_at:
            needs_refresh = True
            logger.info(f"[GARMIN_TOKEN] No expires_at set, refreshing for user_id={user_id}")
        elif integration_obj.token_expires_at < now + timedelta(seconds=buffer_seconds):
            needs_refresh = True
            logger.info(
                f"[GARMIN_TOKEN] Token expires soon ({integration_obj.token_expires_at.isoformat()}), "
                f"refreshing for user_id={user_id}"
            )

        if not needs_refresh:
            # Token still valid, decrypt and return current access token
            try:
                access_token = decrypt_token(integration_obj.access_token)
                expires_at = integration_obj.token_expires_at or (now + timedelta(hours=1))
                logger.debug(f"[GARMIN_TOKEN] Token still valid for user_id={user_id}")
            except EncryptionError as e:
                logger.warning(f"[GARMIN_TOKEN] Failed to decrypt access token, forcing refresh: {e}")
                needs_refresh = True
            else:
                return access_token, expires_at

        # Refresh token
        try:
            logger.info(f"[GARMIN_TOKEN] Refreshing access token for user_id={user_id}")
            token_data = refresh_access_token(
                client_id=settings.garmin_client_id,
                client_secret=settings.garmin_client_secret,
                refresh_token=refresh_token,
            )
        except Exception as e:
            # Check if it's a 400/401 (invalid refresh token)
            status_code = None
            if hasattr(e, "response") and e.response is not None:
                status_code = e.response.status_code

            if status_code in {400, 401}:
                logger.warning(f"[GARMIN_TOKEN] Invalid refresh token for user_id={user_id}, revoking integration")
                # User revoked access - mark integration as revoked
                integration_obj.revoked_at = now
                session.commit()
                raise GarminTokenRefreshError(
                    f"Refresh token invalid for user_id={user_id}. User must re-authorize."
                ) from e

            # Network/Garmin outage - don't revoke token, just raise
            logger.error(f"[GARMIN_TOKEN] Token refresh failed for user_id={user_id}: {e}")
            raise GarminTokenRefreshError(f"Failed to refresh token for user_id={user_id}: {e}") from e

        # Extract new tokens
        new_access_token = token_data.get("access_token")
        new_refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")  # Seconds until expiry

        if not isinstance(new_access_token, str):
            raise GarminTokenRefreshError("Invalid access_token type from Garmin")

        # Calculate expires_at
        if expires_in and isinstance(expires_in, int):
            expires_at = now + timedelta(seconds=expires_in)
        else:
            # Default to 1 hour if not provided
            expires_at = now + timedelta(hours=1)
            logger.warning(f"[GARMIN_TOKEN] No expires_in in response, defaulting to 1 hour for user_id={user_id}")

        # Update integration with new tokens
        integration_obj.access_token = encrypt_token(new_access_token)
        if new_refresh_token and isinstance(new_refresh_token, str):
            integration_obj.refresh_token = encrypt_token(new_refresh_token)
        integration_obj.token_expires_at = expires_at
        session.commit()

        logger.info(f"[GARMIN_TOKEN] Successfully refreshed tokens for user_id={user_id}, expires_at={expires_at.isoformat()}")

        return new_access_token, expires_at


def get_garmin_access_token(user_id: str, buffer_seconds: int = 300) -> str:
    """Get valid Garmin access token, refreshing if needed.

    Args:
        user_id: User ID
        buffer_seconds: Refresh if token expires within this many seconds

    Returns:
        Valid access token

    Raises:
        GarminTokenRefreshError: If refresh fails
    """
    access_token, _ = refresh_garmin_tokens(user_id, buffer_seconds=buffer_seconds)
    return access_token
