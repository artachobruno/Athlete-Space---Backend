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


def _parse_activity_date(activity_date_str: str | int | float) -> datetime | None:
    """Parse activity date from various formats.

    Args:
        activity_date_str: Date string or timestamp

    Returns:
        Parsed datetime or None if parsing fails
    """
    try:
        if isinstance(activity_date_str, str):
            if "T" in activity_date_str:
                return datetime.fromisoformat(activity_date_str.replace("Z", "+00:00"))
            return datetime.strptime(activity_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.fromtimestamp(activity_date_str, tz=timezone.utc)
    except Exception as e:
        logger.debug(f"[GARMIN_CLIENT] Failed to parse activity date {activity_date_str}: {e}")
        return None


def _extract_activity_date(activity: dict[str, Any]) -> datetime | None:
    """Extract and parse activity date from activity dict.

    Args:
        activity: Activity dictionary

    Returns:
        Parsed datetime or None if not found/invalid
    """
    activity_date_str = (
        activity.get("startTimeGMT")
        or activity.get("start_time_gmt")
        or activity.get("startTime")
        or activity.get("start_time")
    )
    if not activity_date_str:
        return None
    return _parse_activity_date(activity_date_str)


def _filter_activities_by_date(
    activities: list[dict[str, Any]],
    start_date: datetime | None,
    end_date: datetime | None,
) -> list[dict[str, Any]]:
    """Filter activities by date range.

    Args:
        activities: List of activity dictionaries
        start_date: Start date filter (inclusive)
        end_date: End date filter (inclusive)

    Returns:
        Filtered list of activities
    """
    if not start_date and not end_date:
        return activities

    filtered_activities = []
    for activity in activities:
        activity_date = _extract_activity_date(activity)
        if not activity_date:
            continue

        if start_date and activity_date < start_date:
            continue
        if end_date and activity_date > end_date:
            continue

        filtered_activities.append(activity)

    return filtered_activities


def _find_oldest_activity_date(activities: list[dict[str, Any]]) -> datetime | None:
    """Find the oldest activity date in a list.

    Args:
        activities: List of activity dictionaries

    Returns:
        Oldest datetime or None if no valid dates found
    """
    oldest_activity = None
    for activity in activities:
        activity_date = _extract_activity_date(activity)
        if activity_date and (oldest_activity is None or activity_date < oldest_activity):
            oldest_activity = activity_date
    return oldest_activity


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
            start_date: Start date for activities (required by API)
            end_date: End date for activities (required by API)
            limit: Number of activities to fetch (max 100)
            offset: Pagination offset

        Returns:
            API response with activities list

        Raises:
            httpx.HTTPError: If API request fails after retry
            GarminTokenRefreshError: If token refresh fails
            ValueError: If dates are not provided (API requires them)
        """
        params: dict[str, Any] = {
            "limit": min(limit, 100),  # Garmin max is typically 100
            "offset": offset,
        }

        # Garmin API REQUIRES time range parameters - they must always be provided
        # Garmin Wellness API expects epoch seconds, NOT ISO date strings
        # Parameter names: startTimeInSeconds, endTimeInSeconds (NOT startDate/endDate)
        if not start_date or not end_date:
            raise ValueError(
                "Garmin API requires both start_date and end_date. "
                f"Provided: start_date={start_date}, end_date={end_date}"
            )

        # Convert to epoch seconds (Garmin API expects this format)
        params["startTimeInSeconds"] = int(start_date.timestamp())
        params["endTimeInSeconds"] = int(end_date.timestamp())

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

            # Auto-refresh on 401 and retry once
            if e.response.status_code == 401 and self._user_id:
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
                activities = _filter_activities_by_date(activities, start_date, end_date)

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
                    oldest_activity = _find_oldest_activity_date(activities)
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
