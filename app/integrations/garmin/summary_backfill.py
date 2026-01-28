"""Garmin Summary Backfill API client.

Implements the Garmin Summary Backfill API:
- Triggers async backfill requests
- Chunks history into 30-day windows
- Handles duplicate requests (HTTP 409)
- Rate-limit aware (by days, not calls)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

from app.integrations.garmin.token_service import GarminTokenRefreshError, get_garmin_access_token

# Garmin Summary Backfill API endpoint
GARMIN_BACKFILL_ACTIVITIES_URL = "https://apis.garmin.com/wellness-api/rest/backfill/activities"

# Maximum date range per request (30 days, inclusive)
MAX_BACKFILL_DAYS = 30


def request_summary_backfill(
    *,
    user_id: str,
    start_time: datetime,
    end_time: datetime,
) -> dict[str, str | int]:
    """Trigger Garmin Summary Backfill.

    Async: returns 202, data arrives later via webhook.
    Does not parse response or expect activities here.

    Args:
        user_id: User ID for token retrieval
        start_time: Start time (UTC, inclusive)
        end_time: End time (UTC, inclusive)

    Returns:
        Dict with status information: {status, status_code, message}

    Raises:
        ValueError: If date range exceeds 30 days
        GarminTokenRefreshError: If token refresh fails
        httpx.HTTPError: If API request fails (non-409 errors)
    """
    # Validate date range (max 30 days)
    date_range = (end_time - start_time).days
    if date_range > MAX_BACKFILL_DAYS:
        raise ValueError(
            f"Date range exceeds maximum of {MAX_BACKFILL_DAYS} days: {date_range} days"
        )

    # Ensure UTC timezone
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    # Convert to seconds (not milliseconds)
    start_time_seconds = int(start_time.timestamp())
    end_time_seconds = int(end_time.timestamp())

    # Get access token
    access_token = get_garmin_access_token(user_id, buffer_seconds=300)

    # Prepare request
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params = {
        "summaryStartTimeInSeconds": start_time_seconds,
        "summaryEndTimeInSeconds": end_time_seconds,
    }

    logger.info(
        f"[GARMIN_BACKFILL] Requesting summary backfill: "
        f"start={start_time.date()}, end={end_time.date()} ({date_range} days)"
    )

    try:
        resp = httpx.get(
            GARMIN_BACKFILL_ACTIVITIES_URL,
            headers=headers,
            params=params,
            timeout=15,
        )

        # Handle expected responses
        if resp.status_code == 202:
            logger.info(
                f"[GARMIN_BACKFILL] Backfill request accepted (202): "
                f"{start_time.date()} to {end_time.date()}"
            )
            return {
                "status": "accepted",
                "status_code": 202,
                "message": "Backfill request accepted",
            }

        if resp.status_code == 409:
            # Duplicate request - this is expected and safe to ignore
            logger.info(
                f"[GARMIN_BACKFILL] Duplicate backfill request (409, ignored): "
                f"{start_time.date()} to {end_time.date()}"
            )
            return {
                "status": "duplicate",
                "status_code": 409,
                "message": "Duplicate request (already requested)",
            }

        # Unexpected status code
        resp.raise_for_status()

    except httpx.HTTPStatusError as e:
        # Handle 401 (token expired) - refresh and retry once
        if e.response.status_code == 401:
            logger.warning(f"[GARMIN_BACKFILL] Token expired (401), refreshing for user_id={user_id}")
            access_token = get_garmin_access_token(user_id)
            headers["Authorization"] = f"Bearer {access_token}"

            # Retry once
            resp = httpx.get(
                GARMIN_BACKFILL_ACTIVITIES_URL,
                headers=headers,
                params=params,
                timeout=15,
            )

            if resp.status_code == 202:
                logger.info(
                    f"[GARMIN_BACKFILL] Backfill request accepted after token refresh (202): "
                    f"{start_time.date()} to {end_time.date()}"
                )
                return {
                    "status": "accepted",
                    "status_code": 202,
                    "message": "Backfill request accepted (after token refresh)",
                }

            if resp.status_code == 409:
                logger.info(
                    f"[GARMIN_BACKFILL] Duplicate backfill request after token refresh (409, ignored): "
                    f"{start_time.date()} to {end_time.date()}"
                )
                return {
                    "status": "duplicate",
                    "status_code": 409,
                    "message": "Duplicate request (already requested)",
                }

            resp.raise_for_status()

        # Re-raise other HTTP errors
        logger.error(
            f"[GARMIN_BACKFILL] API error {e.response.status_code}: "
            f"{e.response.text[:500]} (params={params})"
        )
        raise

    # Should not reach here, but handle gracefully
    logger.warning(f"[GARMIN_BACKFILL] Unexpected response: {resp.status_code}")
    return {
        "status": "unknown",
        "status_code": resp.status_code,
        "message": f"Unexpected status code: {resp.status_code}",
    }


def trigger_full_history_backfill(
    *,
    user_id: str,
    start: datetime,
    end: datetime,
) -> dict[str, int | list[dict[str, str | int]]]:
    """Trigger full history backfill by chunking into 30-day windows.

    Multiple requests allowed. Duplicate windows return HTTP 409 (ignored).
    Rate limit = days requested, not calls.

    Args:
        user_id: User ID for token retrieval
        start: Start date (UTC, inclusive)
        end: End date (UTC, inclusive)

    Returns:
        Dict with results: {total_requests, accepted_count, duplicate_count, errors, results}
    """
    # Ensure UTC timezone
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    total_days = (end - start).days
    logger.info(
        f"[GARMIN_BACKFILL] Triggering full history backfill: "
        f"{start.date()} to {end.date()} ({total_days} days)"
    )

    results: list[dict[str, str | int]] = []
    accepted_count = 0
    duplicate_count = 0
    error_count = 0

    cursor = start
    request_num = 0

    while cursor < end:
        request_num += 1
        # Calculate window end (max 30 days)
        window_end = min(cursor + timedelta(days=MAX_BACKFILL_DAYS), end)
        window_days = (window_end - cursor).days

        logger.info(
            f"[GARMIN_BACKFILL] Request {request_num}: "
            f"{cursor.date()} to {window_end.date()} ({window_days} days)"
        )

        try:
            result = request_summary_backfill(
                user_id=user_id,
                start_time=cursor,
                end_time=window_end,
            )
            results.append(result)

            if result["status"] == "accepted":
                accepted_count += 1
            elif result["status"] == "duplicate":
                duplicate_count += 1
            else:
                error_count += 1

        except Exception as e:
            logger.error(
                f"[GARMIN_BACKFILL] Error in request {request_num} "
                f"({cursor.date()} to {window_end.date()}): {e}"
            )
            error_count += 1
            results.append({
                "status": "error",
                "status_code": 0,
                "message": str(e),
            })

        # Move to next window
        cursor = window_end

    logger.info(
        f"[GARMIN_BACKFILL] Full history backfill complete: "
        f"total_requests={request_num}, accepted={accepted_count}, "
        f"duplicates={duplicate_count}, errors={error_count}"
    )

    return {
        "total_requests": request_num,
        "accepted_count": accepted_count,
        "duplicate_count": duplicate_count,
        "error_count": error_count,
        "results": results,
    }
