"""Garmin Connect API client.

Garmin is NOT a pull API. All activity data must arrive via Push or Ping callbacks.
The system MUST NEVER rely on polling Garmin for activities.

- History fetch: DISABLED. Use Summary Backfill API (summary_backfill.py) + webhooks.
- This client is for: Activity **detail** fetch only (lazy, when user needs GPS/samples).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from app.config.settings import settings
from app.integrations.garmin.token_service import GarminTokenRefreshError, get_garmin_access_token

# Garmin API endpoints. /activities is for detail fetch ONLY (lazy). Never for history.
GARMIN_API_BASE_URL = "https://apis.garmin.com/wellness-api/rest"
GARMIN_ACTIVITIES_URL = f"{GARMIN_API_BASE_URL}/activities"


class GarminClient:
    """Garmin API client for **detail fetch only** (lazy).

    - fetch_activity_detail: Fetch GPS/samples when user opens activity. No history fetch.
    - fetch_activity_summaries / yield_activity_summaries: DISABLED (raise). Use backfill + webhooks.
    - Token refresh on 401.
    """

    def __init__(self, access_token: str, user_id: str | None = None):
        """Initialize Garmin client.

        Args:
            access_token: Decrypted Garmin access token
            user_id: User ID (required for automatic token refresh)
        """
        self._access_token = access_token
        self._user_id = user_id

    def _headers(self) -> dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    def fetch_activity_summaries(
        self,
        *,
        start_date: datetime,
        end_date: datetime,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """DEPRECATED: History fetching via /activities endpoint is disabled.

        Use Summary Backfill API instead (app/integrations/garmin/summary_backfill.py).
        This method raises an error to prevent accidental use.
        """
        raise NotImplementedError(
            "History fetching via /activities endpoint is disabled. "
            "Use Summary Backfill API (app/integrations/garmin/summary_backfill.py) instead. "
            "Data will arrive via webhooks after triggering backfill."
        )

    def fetch_activity_detail(
        self,
        activity_id: str,
        max_samples: int = 10000,
    ) -> dict[str, Any]:
        """Fetch detailed activity data (lazy - only when needed).

        Automatically refreshes token on 401 and retries once.
        Applies sample size guardrails to prevent memory bloat.

        Args:
            activity_id: Garmin activity ID
            max_samples: Maximum number of sample points per stream (default: 10000)

        Returns:
            Activity detail with samples (if available, capped at max_samples)

        Raises:
            httpx.HTTPError: If API request fails after retry
            GarminTokenRefreshError: If token refresh fails
        """
        logger.debug(f"[GARMIN_CLIENT] Fetching activity detail: {activity_id} (max_samples={max_samples})")

        try:
            resp = httpx.get(
                f"{GARMIN_ACTIVITIES_URL}/{activity_id}",
                headers=self._headers(),
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Auto-refresh on 401 and retry once
            if e.response.status_code == 401 and self._user_id:
                logger.warning(f"[GARMIN_CLIENT] Token expired (401), refreshing for user_id={self._user_id}")
                self._access_token = get_garmin_access_token(self._user_id)
                # Retry once
                resp = httpx.get(
                    f"{GARMIN_ACTIVITIES_URL}/{activity_id}",
                    headers=self._headers(),
                    timeout=15,
                )
                resp.raise_for_status()
            else:
                raise

        data = resp.json()

        # Apply sample size guardrails (cap streams to prevent memory bloat)
        if "streams" in data and isinstance(data["streams"], dict):
            for stream_type, stream_data in data["streams"].items():
                if isinstance(stream_data, list) and len(stream_data) > max_samples:
                    logger.warning(
                        f"[GARMIN_CLIENT] Capping {stream_type} stream from {len(stream_data)} to {max_samples} points"
                    )
                    data["streams"][stream_type] = stream_data[:max_samples]

        logger.debug(f"[GARMIN_CLIENT] Fetched activity detail: {activity_id}")
        return data

    def yield_activity_summaries(
        self,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        per_page: int = 100,
        max_pages: int | None = None,
        sleep_seconds: float = 0.5,
    ) -> Iterator[list[dict[str, Any]]]:
        """DEPRECATED: History fetching via /activities endpoint is disabled.

        Use Summary Backfill API instead (app/integrations/garmin/summary_backfill.py).
        This method raises an error to prevent accidental use.
        """
        raise NotImplementedError(
            "History fetching via /activities endpoint is disabled. "
            "Use Summary Backfill API (app/integrations/garmin/summary_backfill.py) instead. "
            "Data will arrive via webhooks after triggering backfill."
        )


def get_garmin_client(user_id: str) -> GarminClient:
    """Get Garmin client for a user with automatic token refresh.

    Args:
        user_id: User ID

    Returns:
        GarminClient instance with valid access token

    Raises:
        GarminTokenRefreshError: If integration not found or token refresh fails
    """
    # Get valid access token (refreshes if needed)
    access_token = get_garmin_access_token(user_id, buffer_seconds=300)  # 5 min buffer

    # Return client with user_id for automatic refresh on 401
    return GarminClient(access_token=access_token, user_id=user_id)
