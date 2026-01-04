"""History backfill with backward-moving cursor.

Implements a safe backward-moving cursor that:
- Fetches older Strava activities incrementally
- Never re-fetches the same data
- Stops automatically when history is complete
- Is resilient to retries, crashes, and rate limits
"""

from __future__ import annotations

import time

import httpx
import requests
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.encryption import EncryptionError, EncryptionKeyError, decrypt_token, encrypt_token
from app.core.settings import settings
from app.integrations.strava.client import StravaClient
from app.integrations.strava.tokens import refresh_access_token
from app.state.db import get_session
from app.state.models import Activity, StravaAccount


class HistoryBackfillError(Exception):
    """Base exception for history backfill errors."""


class TokenRefreshError(HistoryBackfillError):
    """Raised when token refresh fails."""


class RateLimitError(HistoryBackfillError):
    """Raised when rate limit is hit."""


def _decrypt_refresh_token(account: StravaAccount) -> str:
    """Decrypt refresh token from account.

    Args:
        account: StravaAccount object

    Returns:
        Decrypted refresh token string

    Raises:
        TokenRefreshError: If decryption fails
    """
    try:
        return decrypt_token(account.refresh_token)
    except EncryptionKeyError as e:
        logger.error(f"[HISTORY_BACKFILL] Encryption key mismatch for user_id={account.user_id}: {e}")
        raise TokenRefreshError("Failed to decrypt token: ENCRYPTION_KEY not set or changed. User must re-authenticate.") from e
    except EncryptionError as e:
        logger.error(f"[HISTORY_BACKFILL] Failed to decrypt refresh token for user_id={account.user_id}: {e}")
        raise TokenRefreshError(f"Failed to decrypt refresh token: {e}") from e


def _refresh_token_with_strava(refresh_token: str, user_id: str) -> dict:
    """Refresh access token with Strava API.

    Args:
        refresh_token: Decrypted refresh token
        user_id: User ID for logging

    Returns:
        Token data dictionary from Strava

    Raises:
        TokenRefreshError: If refresh fails
        RateLimitError: If rate limited
    """
    try:
        return refresh_access_token(
            client_id=settings.strava_client_id,
            client_secret=settings.strava_client_secret,
            refresh_token=refresh_token,
        )
    except requests.HTTPError as e:
        if e.response is not None:
            status_code = e.response.status_code
            if status_code in {400, 401}:
                logger.warning(f"[HISTORY_BACKFILL] Invalid refresh token for user_id={user_id}")
                raise TokenRefreshError("Invalid refresh token. User must reconnect Strava.") from e
            if status_code == 429:
                logger.warning(f"[HISTORY_BACKFILL] Rate limited during token refresh for user_id={user_id}")
                raise RateLimitError("Rate limited during token refresh") from e
        logger.error(f"[HISTORY_BACKFILL] Token refresh failed for user_id={user_id}: {e}")
        raise TokenRefreshError(f"Token refresh failed: {e}") from e


def _validate_token_data(token_data: dict) -> tuple[str, str | None, int]:
    """Validate and extract token data from Strava response.

    Args:
        token_data: Token data dictionary from Strava

    Returns:
        Tuple of (access_token, refresh_token, expires_at)

    Raises:
        TokenRefreshError: If validation fails
    """
    new_access_token = token_data.get("access_token")
    new_refresh_token = token_data.get("refresh_token")
    new_expires_at = token_data.get("expires_at")

    if not isinstance(new_access_token, str):
        raise TokenRefreshError("Invalid access_token type from Strava")

    if not isinstance(new_expires_at, int):
        raise TokenRefreshError("Invalid expires_at type from Strava")

    return new_access_token, new_refresh_token, new_expires_at


def _rotate_refresh_token(account: StravaAccount, new_refresh_token: str, new_expires_at: int, session) -> None:
    """Update refresh token in account (token rotation).

    Args:
        account: StravaAccount object
        new_refresh_token: New refresh token to store
        new_expires_at: New expiration timestamp
        session: Database session
    """
    try:
        account.refresh_token = encrypt_token(new_refresh_token)
        account.expires_at = new_expires_at
        session.commit()
        logger.info(f"[HISTORY_BACKFILL] Rotated refresh token for user_id={account.user_id}")
    except EncryptionError as e:
        logger.error(f"[HISTORY_BACKFILL] Failed to encrypt new refresh token: {e}")


def _get_access_token_from_account(account: StravaAccount, session) -> str:
    """Get valid access token from StravaAccount, refreshing if needed.

    Args:
        account: StravaAccount object
        session: Database session

    Returns:
        Valid access token string

    Raises:
        TokenRefreshError: If token refresh fails
    """
    refresh_token = _decrypt_refresh_token(account)
    token_data = _refresh_token_with_strava(refresh_token, account.user_id)
    new_access_token, new_refresh_token, new_expires_at = _validate_token_data(token_data)

    if new_refresh_token and isinstance(new_refresh_token, str):
        _rotate_refresh_token(account, new_refresh_token, new_expires_at, session)

    return new_access_token


def _determine_before_parameter(account: StravaAccount) -> int:
    """Determine the `before` parameter for API call.

    Args:
        account: StravaAccount object

    Returns:
        Unix timestamp to use as `before` parameter
    """
    if account.oldest_synced_at is None:
        before = int(time.time())
        logger.info(f"[HISTORY_BACKFILL] Initializing cursor: before={before} (current time)")
    else:
        before = account.oldest_synced_at
        logger.info(f"[HISTORY_BACKFILL] Resuming cursor: before={before}")
    return before


def _fetch_activities_safely(client: StravaClient, before: int, user_id: str) -> list:
    """Fetch activities with error handling.

    Args:
        client: StravaClient instance
        before: Unix timestamp for `before` parameter
        user_id: User ID for logging

    Returns:
        List of StravaActivity objects

    Raises:
        RateLimitError: If rate limit is hit
        HistoryBackfillError: If fetch fails
    """
    try:
        activities = client.get_activities(before=before, per_page=200)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 429:
            logger.warning(f"[HISTORY_BACKFILL] Rate limit hit for user_id={user_id}, aborting without updating cursor")
            raise RateLimitError("Rate limit hit during activity fetch") from e
        logger.error(f"[HISTORY_BACKFILL] Failed to fetch activities for user_id={user_id}: {e}")
        raise HistoryBackfillError(f"Failed to fetch activities: {e}") from e
    return activities


def _build_raw_json(activity) -> dict | None:
    """Build raw_json from activity data.

    Args:
        activity: StravaActivity object

    Returns:
        Dictionary with raw data or None
    """
    if activity.raw:
        return activity.raw
    if activity.average_heartrate is not None or activity.average_watts is not None:
        raw_json: dict = {}
        if activity.average_heartrate is not None:
            raw_json["average_heartrate"] = activity.average_heartrate
        if activity.average_watts is not None:
            raw_json["average_watts"] = activity.average_watts
        return raw_json
    return None


def _save_activities_batch(session, activities: list, user_id: str, athlete_id: str) -> int:
    """Save activities to database (idempotent).

    Args:
        session: Database session
        activities: List of StravaActivity objects
        user_id: User ID
        athlete_id: Strava athlete ID (string)

    Returns:
        Number of activities saved
    """
    saved_count = 0
    for activity in activities:
        try:
            strava_id = str(activity.id)
            raw_json = _build_raw_json(activity)

            activity_obj = Activity(
                user_id=user_id,
                athlete_id=athlete_id,
                strava_activity_id=strava_id,
                source="strava",
                start_time=activity.start_date,
                type=activity.type.capitalize(),
                duration_seconds=activity.elapsed_time,
                distance_meters=activity.distance,
                elevation_gain_meters=activity.total_elevation_gain,
                raw_json=raw_json,
            )
            session.add(activity_obj)
            saved_count += 1
        except IntegrityError:
            session.rollback()
            continue
        except Exception as e:
            logger.error(f"[HISTORY_BACKFILL] Failed to save activity {activity.id} for user_id={user_id}: {e}")
            session.rollback()
            continue

    try:
        session.commit()
    except Exception as e:
        logger.error(f"[HISTORY_BACKFILL] Failed to commit activities for user_id={user_id}: {e}")
        session.rollback()
        raise HistoryBackfillError(f"Failed to commit activities: {e}") from e

    return saved_count


def _update_cursor(session, account: StravaAccount, activities: list, user_id: str) -> None:
    """Update cursor after successful batch save.

    Args:
        session: Database session
        account: StravaAccount object
        activities: List of StravaActivity objects
        user_id: User ID for logging

    Raises:
        HistoryBackfillError: If cursor validation fails
    """
    new_oldest = min(int(act.start_date.timestamp()) for act in activities)
    logger.info(f"[HISTORY_BACKFILL] Updating cursor: oldest_synced_at={account.oldest_synced_at} -> {new_oldest}")

    if account.oldest_synced_at is not None and new_oldest >= account.oldest_synced_at:
        logger.error(f"[HISTORY_BACKFILL] Cursor violation: new_oldest={new_oldest} >= current={account.oldest_synced_at}")
        raise HistoryBackfillError("Cursor moved forward - this should never happen")

    account.oldest_synced_at = new_oldest
    session.add(account)
    session.commit()
    logger.info(f"[HISTORY_BACKFILL] Cursor updated successfully for user_id={user_id}, oldest_synced_at={new_oldest}")


def backfill_user_history(user_id: str) -> None:
    """Backfill user's Strava activity history with backward-moving cursor.

    Cursor rules:
    - Cursor moves backward in time
    - Cursor always points to earliest known activity
    - Cursor is monotonic decreasing
    - Cursor is persisted after every successful chunk

    Args:
        user_id: Clerk user ID (string)

    Raises:
        HistoryBackfillError: If backfill fails
        RateLimitError: If rate limit is hit (cursor not updated)
        TokenRefreshError: If token refresh fails (requires user reconnection)
    """
    logger.info(f"[HISTORY_BACKFILL] Starting history backfill for user_id={user_id}")

    with get_session() as session:
        account_result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

        if not account_result:
            logger.warning(f"[HISTORY_BACKFILL] No Strava account found for user_id={user_id}")
            raise HistoryBackfillError(f"No Strava account found for user_id={user_id}")

        account = account_result[0]

        # Check if full history is already synced
        if account.full_history_synced:
            logger.info(f"[HISTORY_BACKFILL] Full history already synced for user_id={user_id}, skipping")
            return

        # Determine `before` parameter
        before = _determine_before_parameter(account)

        # Get access token
        try:
            access_token = _get_access_token_from_account(account, session)
        except TokenRefreshError as e:
            logger.error(f"[HISTORY_BACKFILL] Token refresh failed for user_id={user_id}: {e}")
            raise

        # Create Strava client and fetch activities
        client = StravaClient(access_token=access_token)
        activities = _fetch_activities_safely(client, before, user_id)

        # If no activities returned, mark as complete
        if not activities:
            logger.info(f"[HISTORY_BACKFILL] No activities returned, marking full_history_synced=True for user_id={user_id}")
            account.full_history_synced = True
            session.add(account)
            session.commit()
            logger.info(f"[HISTORY_BACKFILL] History backfill complete for user_id={user_id}")
            return

        logger.info(f"[HISTORY_BACKFILL] Fetched {len(activities)} activities for user_id={user_id}")

        # Save activities (idempotent - insert blindly, let DB enforce uniqueness)
        saved_count = _save_activities_batch(session, activities, user_id, account.athlete_id)
        logger.info(f"[HISTORY_BACKFILL] Saved {saved_count}/{len(activities)} activities for user_id={user_id}")

        # Update cursor after successful batch
        _update_cursor(session, account, activities, user_id)
