from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import httpx

from app.integrations.strava.client import StravaClient
from app.integrations.strava.token_service import (
    TokenRefreshError,
    TokenServiceError,
    get_access_token_for_athlete,
)
from app.state.db import get_session

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
    with get_session() as session:
        try:
            access_token = get_access_token_for_athlete(
                athlete_id=athlete_id,
                session=session,
            )
        except TokenRefreshError as e:
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
    client = get_strava_client(athlete_id)

    try:
        return operation(client)
    except httpx.HTTPStatusError as e:
        # Check if it's a 401 (expired token)
        if e.response is not None and e.response.status_code == 401:
            if max_retries > 0:
                # Token expired mid-request - refresh and retry once
                client = get_strava_client(athlete_id)
                return operation(client)
            raise
        raise
