from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import httpx
from loguru import logger

from app.db.session import get_session
from app.integrations.strava.client import StravaClient
from app.integrations.strava.token_service import (
    TokenRefreshError,
    TokenServiceError,
    get_access_token_for_athlete,
)

T = TypeVar("T")


def get_strava_client(athlete_id: int) -> StravaClient:
    """Get Strava client for athlete, refreshing token if needed.

    Returns a client with a valid access token. The access token is ephemeral
    and not persisted - it's refreshed on demand from the stored refresh token.

    Args:
        athlete_id: Strava athlete ID

    Returns:
        StravaClient configured with valid access token

    Raises:
        TokenServiceError: If token retrieval or refresh fails
    """
    logger.info(f"[STRAVA_SERVICE] Getting Strava client for athlete_id={athlete_id}")
    with get_session() as session:
        try:
            access_token = get_access_token_for_athlete(
                athlete_id=athlete_id,
                session=session,
            )
            logger.info(f"[STRAVA_SERVICE] Successfully obtained access token for athlete_id={athlete_id}")
        except TokenRefreshError as e:
            logger.error(f"[STRAVA_SERVICE] Failed to get access token for athlete_id={athlete_id}: {e}")
            # Re-raise with more context
            raise TokenServiceError(f"Failed to get access token for athlete_id={athlete_id}: {e}") from e

        return StravaClient(access_token=access_token)


def execute_with_token_retry(  # noqa: UP047
    athlete_id: int,
    operation: Callable[[StravaClient], T],
    *,
    max_retries: int = 1,
) -> T:
    """Execute an operation with automatic token refresh on 401 errors.

    If an operation fails with a 401 (expired token), this will:
    1. Refresh the access token
    2. Retry the operation once
    3. Bubble error if still failing

    Args:
        athlete_id: Strava athlete ID
        operation: Callable that takes a StravaClient and returns a result
        max_retries: Maximum number of retry attempts (default: 1)

    Returns:
        Result of the operation

    Raises:
        TokenServiceError: If token refresh fails
        httpx.HTTPStatusError: If operation fails after retries
    """
    logger.info(f"[STRAVA_SERVICE] Executing operation with token retry for athlete_id={athlete_id} (max_retries={max_retries})")
    client = get_strava_client(athlete_id)

    try:
        result = operation(client)
        logger.info(f"[STRAVA_SERVICE] Operation completed successfully for athlete_id={athlete_id}")
    except httpx.HTTPStatusError as e:
        # Check if it's a 401 (expired token)
        if e.response is not None and e.response.status_code == 401:
            if max_retries > 0:
                logger.warning(f"[STRAVA_SERVICE] Token expired (401), refreshing and retrying for athlete_id={athlete_id}")
                # Token expired mid-request - refresh and retry once
                client = get_strava_client(athlete_id)
                result = operation(client)
                logger.info(f"[STRAVA_SERVICE] Retry operation completed successfully for athlete_id={athlete_id}")
                return result
            logger.error(f"[STRAVA_SERVICE] Token refresh failed after max retries for athlete_id={athlete_id}")
            raise
        status_code = e.response.status_code if e.response else "unknown"
        logger.error(f"[STRAVA_SERVICE] Operation failed with status {status_code} for athlete_id={athlete_id}")
        raise
    else:
        return result
