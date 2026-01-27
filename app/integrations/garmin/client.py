"""Garmin Connect API client.

Thin client for Garmin API operations:
- Activity summaries (no samples)
- Paginated fetch
- Rate-limit aware
- Memory-efficient
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from app.config.settings import settings
from app.integrations.garmin.token_service import GarminTokenRefreshError, get_garmin_access_token

# Garmin API endpoints
# NOTE: Verify these endpoints match actual Garmin Connect API documentation
# Current endpoints based on Garmin Connect API v1:
# - Activity summaries: GET /wellness-api/rest/activities
# - Activity detail: GET /wellness-api/rest/activities/{activityId}
# Update these if Garmin API changes or if using different API version
GARMIN_API_BASE_URL = "https://apis.garmin.com/wellness-api/rest"
GARMIN_ACTIVITIES_URL = f"{GARMIN_API_BASE_URL}/activities"


class GarminClient:
    """Thin Garmin API client.

    - Paginated fetch
    - Rate-limit aware
    - Summary-only (no samples)
    - Automatic token refresh on 401
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
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Fetch activity summaries (no samples).

        Automatically refreshes token on 401 and retries once.

        Args:
            start_date: Start date for activities (optional)
            end_date: End date for activities (optional)
            limit: Number of activities to fetch (max 100)
            offset: Pagination offset

        Returns:
            API response with activities list

        Raises:
            httpx.HTTPError: If API request fails after retry
            GarminTokenRefreshError: If token refresh fails
        """
        params: dict[str, Any] = {
            "limit": min(limit, 100),  # Garmin max is typically 100
            "offset": offset,
        }

        # Garmin API date parameters
        # Try multiple formats: the API might expect different parameter names or formats
        if start_date and end_date:
            # Try ISO 8601 date format first (YYYY-MM-DD)
            # Some Garmin API versions expect this format
            params["startDate"] = start_date.strftime("%Y-%m-%d")
            params["endDate"] = end_date.strftime("%Y-%m-%d")
            
            # Also try alternative parameter names (some API versions use these)
            # params["start"] = start_date.strftime("%Y-%m-%d")
            # params["end"] = end_date.strftime("%Y-%m-%d")
        elif start_date or end_date:
            # If only one is provided, don't send either (API requires both)
            logger.warning(
                "[GARMIN_CLIENT] Both startDate and endDate required by API, "
                f"but only one provided (start={start_date}, end={end_date}). "
                "Fetching without date filter."
            )

        logger.debug(f"[GARMIN_CLIENT] Fetching activity summaries (limit={limit}, offset={offset})")

        try:
            resp = httpx.get(
                GARMIN_ACTIVITIES_URL,
                headers=self._headers(),
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Log error response for debugging
            error_body = ""
            try:
                error_body = e.response.text
                logger.error(
                    f"[GARMIN_CLIENT] API error {e.response.status_code}: {error_body[:500]} "
                    f"(params: {params})"
                )
            except Exception:
                logger.error(
                    f"[GARMIN_CLIENT] API error {e.response.status_code} (could not read response body) "
                    f"(params: {params})"
                )

            # If 400 error with date parameters, try without dates as fallback
            if e.response.status_code == 400 and "startDate" in params:
                logger.warning(
                    "[GARMIN_CLIENT] Date parameters rejected by API, trying without date filter "
                    "(will filter client-side)"
                )
                # Remove date parameters and retry
                fallback_params = {k: v for k, v in params.items() if k not in ("startDate", "endDate")}
                resp = httpx.get(
                    GARMIN_ACTIVITIES_URL,
                    headers=self._headers(),
                    params=fallback_params,
                    timeout=15,
                )
                resp.raise_for_status()
                # Note: We'll filter by date client-side in yield_activity_summaries if needed
            # Auto-refresh on 401 and retry once
            elif e.response.status_code == 401 and self._user_id:
                logger.warning(f"[GARMIN_CLIENT] Token expired (401), refreshing for user_id={self._user_id}")
                self._access_token = get_garmin_access_token(self._user_id)
                # Retry once
                resp = httpx.get(
                    GARMIN_ACTIVITIES_URL,
                    headers=self._headers(),
                    params=params,
                    timeout=15,
                )
                resp.raise_for_status()
            else:
                raise

        data = resp.json()

        logger.info(f"[GARMIN_CLIENT] Fetched {len(data.get('activities', []))} activity summaries")
        return data

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
        """Yield activity summaries page by page (generator).

        Memory-efficient: yields activities as they're fetched.
        Respects rate limits with sleep between pages.

        Args:
            start_date: Start date for activities
            end_date: End date for activities
            per_page: Number of activities per page (max 100)
            max_pages: Maximum pages to fetch (None = no limit)
            sleep_seconds: Seconds to sleep between pages (rate limit safety)

        Yields:
            Activity summary dicts, one page at a time
        """
        logger.info(
            f"[GARMIN_CLIENT] Yielding activity summaries "
            f"(start_date={start_date}, end_date={end_date}, per_page={per_page})"
        )

        page = 0
        total_fetched = 0

        while max_pages is None or page < max_pages:
            if page > 0:
                # Sleep between pages to respect rate limits
                time.sleep(sleep_seconds)

            logger.debug(f"[GARMIN_CLIENT] Fetching page {page + 1}")

            try:
                data = self.fetch_activity_summaries(
                    start_date=start_date,
                    end_date=end_date,
                    limit=per_page,
                    offset=page * per_page,
                )

                activities = data.get("activities", [])
                if not activities:
                    logger.info(f"[GARMIN_CLIENT] No more activities (page {page + 1} was empty)")
                    break

                # Filter by date client-side if date parameters were not used
                # (API might not support date filtering, so we filter after fetching)
                if start_date or end_date:
                    filtered_activities = []
                    for activity in activities:
                        # Extract activity date from various possible fields
                        activity_date_str = (
                            activity.get("startTimeGMT")
                            or activity.get("start_time_gmt")
                            or activity.get("startTime")
                            or activity.get("start_time")
                        )
                        if not activity_date_str:
                            # Skip if no date found
                            continue

                        try:
                            # Parse date (handle various formats)
                            if isinstance(activity_date_str, str):
                                # Try ISO format first
                                if "T" in activity_date_str:
                                    activity_date = datetime.fromisoformat(
                                        activity_date_str.replace("Z", "+00:00")
                                    )
                                else:
                                    # Try date-only format
                                    activity_date = datetime.strptime(activity_date_str, "%Y-%m-%d").replace(
                                        tzinfo=timezone.utc
                                    )
                            else:
                                # Assume it's already a datetime or timestamp
                                activity_date = datetime.fromtimestamp(activity_date_str, tz=timezone.utc)

                            # Filter by date range
                            if start_date and activity_date < start_date:
                                continue
                            if end_date and activity_date > end_date:
                                continue

                            filtered_activities.append(activity)
                        except Exception as e:
                            logger.warning(f"[GARMIN_CLIENT] Failed to parse activity date: {e}, skipping activity")
                            continue

                    activities = filtered_activities

                total_fetched += len(activities)
                logger.info(f"[GARMIN_CLIENT] Fetched {len(activities)} activities from page {page + 1}")

                yield activities

                # If we got fewer than per_page, we've reached the end
                if len(activities) < per_page:
                    logger.info(f"[GARMIN_CLIENT] Reached end of activities (got {len(activities)} < {per_page})")
                    break

                # If filtering by date client-side and we've gone past start_date, stop
                # (activities are typically returned in reverse chronological order)
                if start_date and activities:
                    # Check the oldest activity in this batch
                    oldest_activity = None
                    for activity in activities:
                        activity_date_str = (
                            activity.get("startTimeGMT")
                            or activity.get("start_time_gmt")
                            or activity.get("startTime")
                            or activity.get("start_time")
                        )
                        if activity_date_str:
                            try:
                                if isinstance(activity_date_str, str):
                                    if "T" in activity_date_str:
                                        activity_date = datetime.fromisoformat(
                                            activity_date_str.replace("Z", "+00:00")
                                        )
                                    else:
                                        activity_date = datetime.strptime(activity_date_str, "%Y-%m-%d").replace(
                                            tzinfo=timezone.utc
                                        )
                                else:
                                    activity_date = datetime.fromtimestamp(activity_date_str, tz=timezone.utc)

                                if oldest_activity is None or activity_date < oldest_activity:
                                    oldest_activity = activity_date
                            except Exception:
                                pass

                    # If oldest activity is before start_date, we've gone too far back
                    if oldest_activity and oldest_activity < start_date:
                        logger.info(
                            f"[GARMIN_CLIENT] Reached start_date boundary "
                            f"(oldest activity: {oldest_activity.date()} < {start_date.date()})"
                        )
                        break

                page += 1

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning(f"[GARMIN_CLIENT] Rate limited on page {page + 1}, stopping")
                    break
                raise

        logger.info(f"[GARMIN_CLIENT] Finished yielding activities (total: {total_fetched} across {page + 1} pages)")


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
