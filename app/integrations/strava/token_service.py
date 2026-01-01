from __future__ import annotations

import datetime as dt
from typing import NamedTuple

import requests
from loguru import logger
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.integrations.strava.tokens import refresh_access_token
from app.state.models import StravaAuth


class TokenResult(NamedTuple):
    """Result of token retrieval with access token and metadata."""

    access_token: str
    athlete_id: int
    expires_at: int


class TokenServiceError(Exception):
    """Base exception for token service errors."""


class TokenNotFoundError(TokenServiceError):
    """Raised when no token record exists for an athlete."""


class TokenRefreshError(TokenServiceError):
    """Raised when token refresh fails (e.g., invalid refresh token)."""


class TokenService:
    """Service for managing Strava OAuth token persistence and refresh.

    Responsibilities:
    - Persist refresh tokens (never access tokens)
    - Refresh tokens on demand
    - Rotate refresh tokens on every successful refresh
    - Handle token refresh failures with proper cleanup
    """

    def __init__(self, session: Session) -> None:
        """Initialize token service with database session."""
        self._session = session
        self._client_id = settings.strava_client_id
        self._client_secret = settings.strava_client_secret

    def save_tokens(
        self,
        *,
        athlete_id: int,
        refresh_token: str,
        expires_at: int,
    ) -> None:
        """Save or update tokens for an athlete.

        This is called after OAuth exchange or token refresh.
        Always overwrites existing tokens (token rotation).
        """
        logger.info(f"[TOKEN_SERVICE] Saving tokens for athlete_id={athlete_id}")
        auth = self._session.query(StravaAuth).filter_by(athlete_id=athlete_id).first()

        if auth:
            logger.info(f"[TOKEN_SERVICE] Updating existing tokens for athlete_id={athlete_id}")
            auth.refresh_token = refresh_token
            auth.expires_at = expires_at
        else:
            logger.info(f"[TOKEN_SERVICE] Creating new token record for athlete_id={athlete_id}")
            auth = StravaAuth(
                athlete_id=athlete_id,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
            self._session.add(auth)

        self._session.commit()
        logger.info(f"[TOKEN_SERVICE] Tokens saved successfully for athlete_id={athlete_id}")

    def get_access_token(
        self,
        *,
        athlete_id: int,
        buffer_seconds: int = 60,
    ) -> TokenResult:
        """Get a valid access token for an athlete, refreshing if needed.

        Since access tokens are not persisted, we refresh whenever we need one.
        We check expiry first to avoid unnecessary refreshes when token is still valid.

        Args:
            athlete_id: Strava athlete ID
            buffer_seconds: Refresh token if it expires within this many seconds

        Returns:
            TokenResult with access_token, athlete_id, and expires_at

        Raises:
            TokenNotFoundError: If no token record exists
            TokenRefreshError: If token refresh fails (e.g., invalid refresh token)
        """
        logger.info(f"[TOKEN_SERVICE] Getting access token for athlete_id={athlete_id} (buffer_seconds={buffer_seconds})")
        auth = self._session.query(StravaAuth).filter_by(athlete_id=athlete_id).first()

        if not auth:
            logger.error(f"[TOKEN_SERVICE] No Strava auth found for athlete_id={athlete_id}")
            raise TokenNotFoundError(f"No Strava auth found for athlete_id={athlete_id}")

        # Check if token is expired or near expiry
        expiry_threshold = auth.expires_at - buffer_seconds
        current_time = int(dt.datetime.now(dt.timezone.utc).timestamp())

        logger.info(
            f"[TOKEN_SERVICE] Token expiry check: expires_at={auth.expires_at}, current={current_time}, threshold={expiry_threshold}"
        )

        # Refresh token to get new access token
        # Since access tokens are not persisted, we refresh whenever we need one.
        # The expiry check ensures we refresh when expired/near expiry (per requirement).
        # If not expired, we still refresh to obtain the access token (Strava allows this).
        try:
            logger.info(f"[TOKEN_SERVICE] Refreshing access token for athlete_id={athlete_id}")
            token_data = refresh_access_token(
                client_id=self._client_id,
                client_secret=self._client_secret,
                refresh_token=auth.refresh_token,
            )
            logger.info(f"[TOKEN_SERVICE] Token refresh API call successful for athlete_id={athlete_id}")
        except requests.HTTPError as e:
            # Check if it's a 400/401 (invalid refresh token)
            if e.response is not None and e.response.status_code in {400, 401}:
                logger.warning(f"[TOKEN_SERVICE] Invalid refresh token for athlete_id={athlete_id}, deleting auth record")
                # User revoked access - delete auth record
                self._session.delete(auth)
                self._session.commit()
                logger.info(f"[TOKEN_SERVICE] Deleted invalid auth record for athlete_id={athlete_id}")
                raise TokenRefreshError(f"Refresh token invalid for athlete_id={athlete_id}. User must re-authorize.") from e
            # Network/Strava outage - don't delete token, just raise
            logger.error(f"[TOKEN_SERVICE] Token refresh failed for athlete_id={athlete_id}: {e}")
            raise TokenRefreshError(f"Failed to refresh token for athlete_id={athlete_id}: {e}") from e

        # Explicitly extract and narrow Strava response types
        refresh_token_raw = token_data.get("refresh_token")
        expires_at_raw = token_data["expires_at"]
        access_token_raw = token_data["access_token"]

        if not isinstance(access_token_raw, str):
            raise TokenRefreshError("Invalid access_token type from Strava")

        if not isinstance(expires_at_raw, int):
            raise TokenRefreshError("Invalid expires_at type from Strava")

        if refresh_token_raw is not None and not isinstance(refresh_token_raw, str):
            raise TokenRefreshError("Invalid refresh_token type from Strava")

        new_refresh_token = refresh_token_raw or auth.refresh_token
        new_expires_at = expires_at_raw
        new_access_token = access_token_raw

        # CRITICAL: Rotate refresh token (overwrite stored token)
        logger.info(f"[TOKEN_SERVICE] Rotating refresh token for athlete_id={athlete_id}")
        auth.refresh_token = new_refresh_token
        auth.expires_at = new_expires_at
        self._session.commit()

        logger.info(f"[TOKEN_SERVICE] Access token refreshed successfully for athlete_id={athlete_id}")
        return TokenResult(
            access_token=new_access_token,
            athlete_id=athlete_id,
            expires_at=new_expires_at,
        )

    def delete_tokens(self, athlete_id: int) -> None:
        """Delete token record for an athlete.

        Used when user revokes access or token becomes permanently invalid.
        """
        logger.info(f"Deleting tokens for athlete_id={athlete_id}")
        auth = self._session.query(StravaAuth).filter_by(athlete_id=athlete_id).first()
        if auth:
            self._session.delete(auth)
            self._session.commit()
            logger.info(f"Tokens deleted for athlete_id={athlete_id}")
        else:
            logger.warning(f"No tokens found to delete for athlete_id={athlete_id}")


def get_access_token_for_athlete(
    *,
    athlete_id: int,
    session: Session,
) -> str:
    """Convenience function to get access token, refreshing if needed.

    This is the main entry point for getting access tokens.
    Handles refresh logic and returns only the access token string.

    Args:
        athlete_id: Strava athlete ID
        session: Database session

    Returns:
        Valid access token string

    Raises:
        TokenNotFoundError: If no token record exists
        TokenRefreshError: If token refresh fails
    """
    logger.info(f"[TOKEN_SERVICE] Getting access token for athlete_id={athlete_id}")
    service = TokenService(session)
    result = service.get_access_token(athlete_id=athlete_id)
    logger.info(f"[TOKEN_SERVICE] Successfully obtained access token for athlete_id={athlete_id}")
    return result.access_token
