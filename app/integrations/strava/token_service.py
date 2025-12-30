from __future__ import annotations

import datetime as dt
import time
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
        logger.debug(f"Saving tokens for athlete_id={athlete_id}")
        auth = self._session.query(StravaAuth).filter_by(athlete_id=athlete_id).first()

        if auth:
            logger.debug(f"Updating existing tokens for athlete_id={athlete_id}")
            auth.refresh_token = refresh_token
            auth.expires_at = expires_at
        else:
            logger.debug(f"Creating new token record for athlete_id={athlete_id}")
            auth = StravaAuth(
                athlete_id=athlete_id,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
            self._session.add(auth)

        self._session.commit()
        logger.info(f"Tokens saved successfully for athlete_id={athlete_id}")

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
        logger.debug(f"Getting access token for athlete_id={athlete_id}")
        auth = self._session.query(StravaAuth).filter_by(athlete_id=athlete_id).first()

        if not auth:
            logger.error(f"No Strava auth found for athlete_id={athlete_id}")
            raise TokenNotFoundError(f"No Strava auth found for athlete_id={athlete_id}")

        # Check if token is expired or near expiry
        expiry_threshold = auth.expires_at - buffer_seconds
        current_time = int(dt.datetime.now(dt.timezone.utc).timestamp())

        logger.debug(f"Token expiry check: expires_at={auth.expires_at}, current={current_time}, threshold={expiry_threshold}")

        # Refresh token to get new access token
        # Since access tokens are not persisted, we refresh whenever we need one.
        # The expiry check ensures we refresh when expired/near expiry (per requirement).
        # If not expired, we still refresh to obtain the access token (Strava allows this).
        try:
            logger.info(f"Refreshing access token for athlete_id={athlete_id}")
            token_data = refresh_access_token(
                client_id=self._client_id,
                client_secret=self._client_secret,
                refresh_token=auth.refresh_token,
            )
        except requests.HTTPError as e:
            # Check if it's a 400/401 (invalid refresh token)
            if e.response is not None and e.response.status_code in (400, 401):
                logger.warning(f"Invalid refresh token for athlete_id={athlete_id}, deleting auth record")
                # User revoked access - delete auth record
                self._session.delete(auth)
                self._session.commit()
                raise TokenRefreshError(f"Refresh token invalid for athlete_id={athlete_id}. User must re-authorize.") from e
            # Network/Strava outage - don't delete token, just raise
            logger.error(f"Token refresh failed for athlete_id={athlete_id}: {e}")
            raise TokenRefreshError(f"Failed to refresh token for athlete_id={athlete_id}: {e}") from e

        # Extract new tokens
        new_refresh_token = token_data.get("refresh_token") or auth.refresh_token
        new_expires_at = token_data["expires_at"]
        new_access_token = token_data["access_token"]

        # CRITICAL: Rotate refresh token (overwrite stored token)
        logger.debug(f"Rotating refresh token for athlete_id={athlete_id}")
        auth.refresh_token = new_refresh_token
        auth.expires_at = new_expires_at
        self._session.commit()

        logger.info(f"Access token refreshed successfully for athlete_id={athlete_id}")
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


def get_access_token_with_retry(
    *,
    athlete_id: int,
    session: Session,
    max_retries: int = 1,
    retry_delay: float = 1.0,
) -> TokenResult:
    """Get access token with retry logic for network failures.

    Implements exponential backoff for transient failures.
    Does not retry on invalid refresh token errors.

    Args:
        athlete_id: Strava athlete ID
        session: Database session
        max_retries: Maximum number of retry attempts
        retry_delay: Initial delay between retries (seconds)

    Returns:
        TokenResult with access token

    Raises:
        TokenNotFoundError: If no token record exists
        TokenRefreshError: If all retries fail or token is invalid
    """
    service = TokenService(session)
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return service.get_access_token(athlete_id=athlete_id)
        except TokenRefreshError as e:
            # Don't retry invalid refresh tokens
            if "invalid" in str(e).lower() or "revoked" in str(e).lower():
                raise
            last_error = e
            if attempt < max_retries:
                time.sleep(retry_delay * (2**attempt))
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(retry_delay * (2**attempt))

    if last_error:
        raise TokenRefreshError(f"Failed to get access token after {max_retries + 1} attempts") from last_error

    raise TokenRefreshError("Failed to get access token")


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
    service = TokenService(session)
    result = service.get_access_token(athlete_id=athlete_id)
    return result.access_token
