"""Background sync for Strava activities.

Step 5: Automated incremental sync that runs periodically to fetch new activities.
Handles token refresh, rate limiting, and error recovery automatically.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config.settings import settings
from app.core.encryption import EncryptionError, EncryptionKeyError, decrypt_token, encrypt_token
from app.db.models import Activity, StravaAccount
from app.db.session import get_session
from app.integrations.strava.client import StravaClient
from app.integrations.strava.tokens import refresh_access_token
from app.metrics.computation_service import trigger_recompute_on_new_activities
from app.workouts.guards import assert_activity_has_execution, assert_activity_has_workout
from app.workouts.workout_factory import WorkoutFactory


class SyncError(Exception):
    """Base exception for sync errors."""


class TokenRefreshError(SyncError):
    """Raised when token refresh fails."""


class RateLimitError(SyncError):
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
        logger.error(f"[SYNC] Encryption key mismatch for user_id={account.user_id}: {e!s}")
        raise TokenRefreshError("Failed to decrypt token: ENCRYPTION_KEY not set or changed. User must re-authenticate with Strava.") from e
    except EncryptionError as e:
        logger.error(f"[SYNC] Failed to decrypt refresh token for user_id={account.user_id}: {e!s}")
        raise TokenRefreshError(f"Failed to decrypt refresh token: {e!s}") from e


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
                logger.warning(f"[SYNC] Invalid refresh token for user_id={user_id}")
                raise TokenRefreshError("Invalid refresh token. User must reconnect Strava.") from e
            if status_code == 429:
                logger.warning(f"[SYNC] Rate limited during token refresh for user_id={user_id}")
                raise RateLimitError("Rate limited during token refresh") from e
        logger.error(f"[SYNC] Token refresh failed for user_id={user_id}: {e!s}")
        raise TokenRefreshError(f"Token refresh failed: {e!s}") from e


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
        logger.info(f"[SYNC] Rotated refresh token for user_id={account.user_id}")
    except EncryptionError as e:
        logger.error(f"[SYNC] Failed to encrypt new refresh token: {e!s}")
        # Continue with old refresh token - not critical


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


def _sync_user_activities(user_id: str, account: StravaAccount, session) -> dict[str, int | str]:
    """Sync activities for a single user.

    Args:
        user_id: Clerk user ID
        account: StravaAccount object
        session: Database session

    Returns:
        Dictionary with imported and skipped counts

    Raises:
        TokenRefreshError: If token refresh fails
        RateLimitError: If rate limited
    """
    logger.info(f"[SYNC] Starting sync for user_id={user_id}")

    # Get access token (with refresh if needed)
    try:
        access_token = _get_access_token_from_account(account, session)
    except TokenRefreshError:
        raise
    except Exception as e:
        logger.exception(f"[SYNC] Failed to get access token for user_id={user_id}: {e!s}")
        raise TokenRefreshError(f"Failed to get access token: {e!s}") from e

    # Calculate date range (use last_sync_at if available, otherwise last 90 days)
    now = datetime.now(timezone.utc)
    if account.last_sync_at:
        after_date = datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc)
        # Add 1 second buffer to avoid missing activities
        after_date += timedelta(seconds=1)

        # Detect large gaps (e.g., sync stopped for months)
        # If last_sync_at is more than 7 days old, extend sync window to cover the gap
        gap_days = (now - after_date).days
        if gap_days > 7:
            logger.info(
                f"[SYNC] Large gap detected: {gap_days} days since last sync. Extending sync window to cover gap and ensure we catch up."
            )
            # Extend sync window to cover the gap, but cap at 90 days to avoid rate limits
            # We'll progressively sync older data via history backfill
            max_sync_window = now - timedelta(days=90)
            after_date = max(after_date, max_sync_window)
            logger.info(f"[SYNC] Extended sync window to {after_date.isoformat()} (covering {gap_days} day gap, capped at 90 days)")
    else:
        # First sync: fetch last 90 days to ensure we have enough data for metrics
        after_date = now - timedelta(days=90)
        logger.info(f"[SYNC] First sync for user_id={user_id}, fetching last 90 days")

    # Always check for recent activities (last 48 hours) to ensure nothing is missing
    # This is a safety check to catch any activities that might have been missed
    recent_check_date = now - timedelta(hours=48)
    if after_date > recent_check_date:
        # If our sync window is very recent, extend it to cover last 48 hours
        logger.info(
            f"[SYNC] Extending sync window to cover last 48 hours for safety check: "
            f"after_date={after_date.isoformat()} -> recent_check_date={recent_check_date.isoformat()}"
        )
        after_date = recent_check_date

    logger.info(f"[SYNC] Fetching activities for user_id={user_id} from {after_date.isoformat()} to {now.isoformat()}")

    # Create Strava client
    client = StravaClient(access_token=access_token)

    # Fetch activities from Strava
    try:
        strava_activities = client.get_activities(after_ts=after_date)
        logger.info(f"[SYNC] Fetched {len(strava_activities)} activities from Strava for user_id={user_id}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            logger.warning(f"[SYNC] Rate limited while fetching activities for user_id={user_id}")
            raise RateLimitError("Rate limited while fetching activities") from e
        logger.exception(f"[SYNC] Failed to fetch activities for user_id={user_id}: {e!s}")
        raise SyncError(f"Failed to fetch activities: {e!s}") from e
    except Exception as e:
        logger.exception(f"[SYNC] Unexpected error fetching activities for user_id={user_id}: {e!s}")
        raise SyncError(f"Unexpected error fetching activities: {e!s}") from e

    # Store activities in database (idempotent upsert)
    imported_count = 0
    skipped_count = 0
    duplicate_count = 0
    created_activities: list[Activity] = []

    for strava_activity in strava_activities:
        strava_id = str(strava_activity.id)

        # Check if activity already exists (prevents duplicates)
        existing = session.execute(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.strava_activity_id == strava_id,
            )
        ).first()

        if existing:
            skipped_count += 1
            logger.debug(f"[SYNC] Activity {strava_id} already exists for user_id={user_id}, skipping")
            continue

        # Extract fields from Strava activity
        start_time_raw = strava_activity.start_date
        if isinstance(start_time_raw, datetime):
            start_time = start_time_raw
        else:
            # Convert to string and handle ISO format
            date_string = str(start_time_raw)
            # Replace Z with +00:00 for ISO format compatibility using string method
            if "Z" in date_string:
                date_string = date_string.replace("Z", "+00:00")
            start_time = datetime.fromisoformat(date_string)

        # Store raw JSON
        raw_json = strava_activity.raw if strava_activity.raw else {}

        # Create new activity record
        activity = Activity(
            user_id=user_id,
            athlete_id=account.athlete_id,
            strava_activity_id=strava_id,
            source="strava",
            start_time=start_time,
            type=strava_activity.type,
            duration_seconds=strava_activity.elapsed_time,
            distance_meters=strava_activity.distance,
            elevation_gain_meters=strava_activity.total_elevation_gain,
            raw_json=raw_json,
        )
        session.add(activity)
        session.flush()  # Ensure ID is generated

        # PHASE 3: Enforce workout + execution creation (mandatory invariant)
        workout = WorkoutFactory.get_or_create_for_activity(session, activity)
        WorkoutFactory.attach_activity(session, workout, activity)

        created_activities.append(activity)
        imported_count += 1

    # Update last_sync_at and success tracking on success
    account.last_sync_at = int(now.timestamp())
    account.sync_success_count = (account.sync_success_count or 0) + 1
    account.last_sync_error = None

    # Commit all changes
    try:
        session.commit()

        # PHASE 7: Assert invariant holds (guard check)
        try:
            for activity in created_activities:
                session.refresh(activity)
                assert_activity_has_workout(activity)
                assert_activity_has_execution(session, activity)
        except AssertionError:
            # Log but don't fail the request - invariant violation is logged
            pass

    except IntegrityError as e:
        # Handle duplicate constraint violations (race condition: activity inserted between check and commit)
        session.rollback()
        logger.warning(
            f"[SYNC] IntegrityError during commit (duplicate detected): {e}. "
            "This may indicate concurrent sync operations. Retrying with individual commits."
        )
        # Retry: commit activities one by one to identify which ones are duplicates
        retry_imported = 0
        retry_duplicate = 0
        for strava_activity in strava_activities:
            strava_id = str(strava_activity.id)
            # Re-check if exists (may have been inserted by another process)
            existing = session.execute(
                select(Activity).where(
                    Activity.user_id == user_id,
                    Activity.strava_activity_id == strava_id,
                )
            ).first()
            if existing:
                retry_duplicate += 1
                continue
            # Re-create activity and try to save individually
            start_time_raw = strava_activity.start_date
            if isinstance(start_time_raw, datetime):
                start_time = start_time_raw
            else:
                date_string = str(start_time_raw)
                if "Z" in date_string:
                    date_string = date_string.replace("Z", "+00:00")
                start_time = datetime.fromisoformat(date_string)
            raw_json = strava_activity.raw if strava_activity.raw else {}
            try:
                activity = Activity(
                    user_id=user_id,
                    athlete_id=account.athlete_id,
                    strava_activity_id=strava_id,
                    source="strava",
                    start_time=start_time,
                    type=strava_activity.type,
                    duration_seconds=strava_activity.elapsed_time,
                    distance_meters=strava_activity.distance,
                    elevation_gain_meters=strava_activity.total_elevation_gain,
                    raw_json=raw_json,
                )
                session.add(activity)
                session.flush()  # Ensure ID is generated

                # PHASE 3: Enforce workout + execution creation (mandatory invariant)
                workout = WorkoutFactory.get_or_create_for_activity(session, activity)
                WorkoutFactory.attach_activity(session, workout, activity)

                session.commit()
                retry_imported += 1
            except IntegrityError:
                session.rollback()
                retry_duplicate += 1
                logger.debug(f"[SYNC] Activity {strava_id} duplicate in retry, skipping")
        # Update counts (retry_duplicate includes activities that were duplicates in retry)
        imported_count = retry_imported
        duplicate_count = retry_duplicate
        skipped_count = 0  # Reset since we're retrying everything
        # Update last_sync_at
        account.last_sync_at = int(now.timestamp())
        session.commit()

    logger.info(
        f"[SYNC] Sync complete for user_id={user_id}: "
        f"imported={imported_count}, skipped={skipped_count}, duplicates={duplicate_count}, "
        f"total_fetched={len(strava_activities)}"
    )

    # Trigger metrics recomputation if new activities were imported
    if imported_count > 0:
        logger.info(f"[SYNC] Triggering metrics recomputation for user_id={user_id} ({imported_count} new activities)")
        try:
            trigger_recompute_on_new_activities(user_id)
        except Exception:
            logger.exception("[SYNC] Failed to trigger metrics recomputation")
            # Don't fail the sync if metrics recomputation fails

    return {
        "imported": imported_count,
        "skipped": skipped_count,
        "total_fetched": len(strava_activities),
    }


def sync_user_activities(user_id: str, max_retries: int = 2) -> dict[str, int | str]:
    """Sync activities for a user with retry logic and exponential backoff.

    Args:
        user_id: Clerk user ID
        max_retries: Maximum number of retry attempts (default: 2)

    Returns:
        Dictionary with sync results or error information
    """
    logger.info(f"[SYNC] Starting sync job for user_id={user_id}")

    with get_session() as session:
        # Get StravaAccount
        account_result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

        if not account_result:
            logger.warning(f"[SYNC] No Strava account found for user_id={user_id}")
            return {"error": "No Strava account connected", "user_id": user_id}

        account = account_result[0]

        # Retry loop with exponential backoff
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                result = _sync_user_activities(user_id, account, session)
                logger.info(f"[SYNC] Sync successful for user_id={user_id} on attempt {attempt + 1}")
            except RateLimitError as e:
                # Rate limit: exponential backoff
                wait_seconds = 2**attempt * 60  # 1min, 2min, 4min
                logger.warning(f"[SYNC] Rate limited for user_id={user_id} on attempt {attempt + 1}, waiting {wait_seconds}s before retry")
                if attempt < max_retries:
                    time.sleep(wait_seconds)
                    last_error = e
                    continue
                logger.error(f"[SYNC] Rate limit exceeded for user_id={user_id} after {max_retries + 1} attempts")
                return {"error": "Rate limit exceeded", "user_id": user_id}
            except TokenRefreshError as e:
                # Token error: don't retry, track failure
                with get_session() as error_session:
                    account_result = error_session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
                    if account_result:
                        error_account = account_result[0]
                        error_account.sync_failure_count = (error_account.sync_failure_count or 0) + 1
                        error_account.last_sync_error = str(e)
                        error_session.commit()
                logger.error(f"[SYNC] Token refresh failed for user_id={user_id}: {e!s}")
                return {"error": "Token refresh failed. User must reconnect Strava.", "user_id": user_id}
            except SyncError as e:
                # Other sync errors: retry with backoff
                wait_seconds = 2**attempt * 5  # 5s, 10s, 20s
                logger.warning(
                    f"[SYNC] Sync error for user_id={user_id} on attempt {attempt + 1}: {e!s}, waiting {wait_seconds}s before retry"
                )
                if attempt < max_retries:
                    time.sleep(wait_seconds)
                    last_error = e
                    continue
                # Track failure
                with get_session() as error_session:
                    account_result = error_session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
                    if account_result:
                        error_account = account_result[0]
                        error_account.sync_failure_count = (error_account.sync_failure_count or 0) + 1
                        error_account.last_sync_error = str(e)
                        error_session.commit()
                logger.error(f"[SYNC] Sync failed for user_id={user_id} after {max_retries + 1} attempts: {e!s}")
                return {"error": f"Sync failed: {e!s}", "user_id": user_id}
            except Exception as e:
                # Track failure
                with get_session() as error_session:
                    account_result = error_session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
                    if account_result:
                        error_account = account_result[0]
                        error_account.sync_failure_count = (error_account.sync_failure_count or 0) + 1
                        error_account.last_sync_error = str(e)
                        error_session.commit()
                logger.exception(f"[SYNC] Unexpected error for user_id={user_id}: {e!s}")
                return {"error": f"Unexpected error: {e!s}", "user_id": user_id}
            else:
                # Success: return result
                return result

        # Should not reach here, but handle it
        error_msg = str(last_error) if last_error else "Unknown error"
        return {"error": f"Sync failed after {max_retries + 1} attempts: {error_msg}", "user_id": user_id}


def sync_all_users() -> dict[str, int | list[dict[str, int | str]]]:
    """Sync activities for all users with Strava accounts.

    Returns:
        Dictionary with total users synced and results per user
    """
    logger.info("[SYNC] Starting sync for all users")

    with get_session() as session:
        accounts = session.execute(select(StravaAccount)).all()

        if not accounts:
            logger.info("[SYNC] No Strava accounts found to sync")
            return {"total_users": 0, "results": []}

        logger.info(f"[SYNC] Found {len(accounts)} user(s) to sync")

        results = []
        for account_row in accounts:
            account = account_row[0]
            user_id = account.user_id

            try:
                result = sync_user_activities(user_id)
                results.append(result)
            except Exception as e:
                logger.exception(f"[SYNC] Failed to sync user_id={user_id}: {e!s}")
                results.append({"error": f"Failed to sync: {e!s}", "user_id": user_id})

        successful = sum(1 for r in results if "error" not in r)
        logger.info(f"[SYNC] Sync complete: {successful}/{len(accounts)} users synced successfully")

        return {
            "total_users": len(accounts),
            "successful": successful,
            "failed": len(accounts) - successful,
            "results": results,
        }
