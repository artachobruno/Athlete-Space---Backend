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
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.core.encryption import EncryptionError, EncryptionKeyError, decrypt_token, encrypt_token
from app.db.models import Activity, StravaAccount, UserSettings
from app.db.session import get_session
from app.integrations.strava.client import StravaClient
from app.integrations.strava.tokens import refresh_access_token
from app.metrics.computation_service import trigger_recompute_on_new_activities
from app.metrics.load_computation import AthleteThresholds, compute_activity_tss
from app.pairing.auto_pairing_service import try_auto_pair
from app.utils.sport_utils import normalize_sport_type
from app.utils.title_utils import normalize_activity_title
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
        new_expires_at: New expiration timestamp (epoch seconds)
        session: Database session
    """
    try:
        account.refresh_token = encrypt_token(new_refresh_token)
        # Convert epoch seconds to datetime (database expects TIMESTAMPTZ)
        expires_at_dt = datetime.fromtimestamp(new_expires_at, tz=timezone.utc)
        account.expires_at = expires_at_dt
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

    # Memory monitoring for sync operations
    try:
        from app.core.system_memory import log_memory_snapshot
        log_memory_snapshot("sync_start")
    except Exception:
        pass  # Don't fail if memory monitoring fails

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
        # Use last_sync_at as the starting point - this is the timestamp of the newest activity we've already synced
        # We fetch activities AFTER this timestamp to get only new activities
        after_date = account.last_sync_at
        # Add 1 second buffer to avoid missing activities that might have the exact same timestamp
        after_date += timedelta(seconds=1)

        logger.info(
            f"[SYNC] Incremental sync: fetching activities after {after_date.isoformat()} "
            f"(last synced activity was at {account.last_sync_at.isoformat()})"
        )
    else:
        # First sync: fetch last 90 days to ensure we have enough data for metrics
        after_date = now - timedelta(days=90)
        logger.info(f"[SYNC] First sync for user_id={user_id}, fetching last 90 days")

    # Note: We removed the "large gap" and "recent check" logic because:
    # 1. If there's a large gap, history backfill will handle older activities
    # 2. We want to fetch ONLY new activities (after last_sync_at) to avoid refetching
    # 3. The system will continue syncing until all activities are fetched via incremental syncs

    logger.info(f"[SYNC] Fetching activities for user_id={user_id} from {after_date.isoformat()} to {now.isoformat()}")

    # Create Strava client
    client = StravaClient(access_token=access_token)

    # Fetch activities from Strava using generator to avoid loading all into memory
    imported_count = 0
    skipped_count = 0
    duplicate_count = 0
    batch_size = 50  # Process 50 activities at a time to limit memory usage
    batch_activities: list = []
    all_activities_timestamps: list[datetime] = []  # Track timestamps to determine newest

    def _process_batch(batch: list) -> None:
        """Process a batch of activities and commit to database."""
        nonlocal imported_count, skipped_count
        batch_created: list[Activity] = []

        for strava_activity_item in batch:
            strava_id = str(strava_activity_item.id)

            # Check if activity already exists (prevents duplicates)
            existing = session.execute(
                select(Activity).where(
                    Activity.user_id == user_id,
                    Activity.source == "strava",
                    Activity.source_activity_id == str(strava_id),
                )
            ).first()

            if existing:
                skipped_count += 1
                logger.debug(f"[SYNC] Activity {strava_id} already exists for user_id={user_id}, skipping")
                continue

            # Extract fields from Strava activity
            start_time_raw = strava_activity_item.start_date
            if isinstance(start_time_raw, datetime):
                start_time = start_time_raw
            else:
                # Convert to string and handle ISO format
                date_string = str(start_time_raw)
                # Replace Z with +00:00 for ISO format compatibility using string method
                if "Z" in date_string:
                    date_string = date_string.replace("Z", "+00:00")
                start_time = datetime.fromisoformat(date_string)

            # Store raw JSON in metrics
            raw_json = strava_activity_item.raw if strava_activity_item.raw else {}
            metrics_dict: dict = {}
            if raw_json:
                metrics_dict["raw_json"] = raw_json

            # Normalize sport type and title
            sport_type = normalize_sport_type(strava_activity_item.type)
            title = normalize_activity_title(
                strava_title=strava_activity_item.name,
                sport=sport_type,
                distance_meters=strava_activity_item.distance,
                duration_seconds=strava_activity_item.elapsed_time,
            )

            # Create new activity record
            activity = Activity(
                user_id=user_id,
                source="strava",
                source_activity_id=strava_id,
                sport=sport_type,
                title=title,
                starts_at=start_time,
                duration_seconds=strava_activity_item.elapsed_time,
                distance_meters=strava_activity_item.distance,
                elevation_gain_meters=strava_activity_item.total_elevation_gain,
                metrics=metrics_dict,
            )
            session.add(activity)
            session.flush()  # Ensure ID is generated

            # PHASE 3: Enforce workout + execution creation (mandatory invariant)
            workout = WorkoutFactory.get_or_create_for_activity(session, activity)
            WorkoutFactory.attach_activity(session, workout, activity)

            # Compute TSS (works with or without streams_data - uses HR/RPE fallbacks if streams not available)
            try:
                user_settings = session.query(UserSettings).filter_by(user_id=user_id).first()
                athlete_thresholds = _build_athlete_thresholds(user_settings)
                tss = compute_activity_tss(activity, athlete_thresholds)
                activity.tss = tss
                activity.tss_version = "v2"
                logger.debug(
                    f"[SYNC] Computed TSS for activity {strava_id}: tss={tss}, version=v2"
                )
            except Exception as e:
                logger.warning(f"[SYNC] Failed to compute TSS for activity {strava_id}: {e}")

            # Attempt auto-pairing with planned sessions
            try:
                try_auto_pair(activity=activity, session=session)
            except Exception as e:
                logger.warning(f"[SYNC] Auto-pairing failed for activity {strava_id}: {e}")

            batch_created.append(activity)
            imported_count += 1

        # Commit batch to reduce memory usage
        try:
            session.commit()

            # PHASE 7: Assert invariant holds (guard check) for this batch
            try:
                for activity in batch_created:
                    session.refresh(activity)
                    assert_activity_has_workout(activity)
                    assert_activity_has_execution(session, activity)
            except AssertionError:
                # Log but don't fail the request - invariant violation is logged
                pass

            logger.debug(f"[SYNC] Processed batch, imported {len(batch_created)} activities")

        except IntegrityError as e:
            # Handle duplicate constraint violations (race condition: activity inserted between check and commit)
            session.rollback()
            logger.warning(
                f"[SYNC] IntegrityError during batch commit (duplicate detected): {e}. "
                "Retrying batch with individual commits."
            )
            # Retry batch: commit activities one by one to identify which ones are duplicates
            for strava_activity_item in batch:
                strava_id = str(strava_activity_item.id)
                # Re-check if exists (may have been inserted by another process)
                existing = session.execute(
                    select(Activity).where(
                        Activity.user_id == user_id,
                        Activity.source == "strava",
                        Activity.source_activity_id == str(strava_id),
                    )
                ).first()

                if existing:
                    skipped_count += 1
                    continue

                # Re-create activity (simplified - just the essential fields)
                # This is a fallback, so we skip TSS computation and auto-pairing
                start_time_raw = strava_activity_item.start_date
                if isinstance(start_time_raw, datetime):
                    start_time = start_time_raw
                else:
                    date_string = str(start_time_raw)
                    if "Z" in date_string:
                        date_string = date_string.replace("Z", "+00:00")
                    start_time = datetime.fromisoformat(date_string)

                raw_json = strava_activity_item.raw if strava_activity_item.raw else {}
                metrics_dict: dict = {}
                if raw_json:
                    metrics_dict["raw_json"] = raw_json

                sport_type = normalize_sport_type(strava_activity_item.type)
                title = normalize_activity_title(
                    strava_title=strava_activity_item.name,
                    sport=sport_type,
                    distance_meters=strava_activity_item.distance,
                    duration_seconds=strava_activity_item.elapsed_time,
                )

                activity = Activity(
                    user_id=user_id,
                    source="strava",
                    source_activity_id=strava_id,
                    sport=sport_type,
                    title=title,
                    starts_at=start_time,
                    duration_seconds=strava_activity_item.elapsed_time,
                    distance_meters=strava_activity_item.distance,
                    elevation_gain_meters=strava_activity_item.total_elevation_gain,
                    metrics=metrics_dict,
                )
                session.add(activity)
                session.flush()

                workout = WorkoutFactory.get_or_create_for_activity(session, activity)
                WorkoutFactory.attach_activity(session, workout, activity)

                try:
                    session.commit()
                    imported_count += 1
                except IntegrityError:
                    session.rollback()
                    skipped_count += 1

    # Fetch activities using generator and process in batches
    try:
        activity_generator = client.yield_activities(after_ts=after_date)
        total_fetched = 0

        for strava_activity in activity_generator:
            total_fetched += 1
            all_activities_timestamps.append(strava_activity.start_date)
            batch_activities.append(strava_activity)

            # Process batch when it reaches batch_size
            if len(batch_activities) >= batch_size:
                _process_batch(batch_activities)
                batch_activities = []

        # Process any remaining activities in the final batch
        if batch_activities:
            _process_batch(batch_activities)

        logger.info(f"[SYNC] Fetched {total_fetched} activities from Strava for user_id={user_id}")

    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            logger.warning(f"[SYNC] Rate limited while fetching activities for user_id={user_id}")
            raise RateLimitError("Rate limited while fetching activities") from e
        logger.exception(f"[SYNC] Failed to fetch activities for user_id={user_id}: {e!s}")
        raise SyncError(f"Failed to fetch activities: {e!s}") from e
    except Exception as e:
        logger.exception(f"[SYNC] Unexpected error fetching activities for user_id={user_id}: {e!s}")
        raise SyncError(f"Unexpected error fetching activities: {e!s}") from e

    # Determine the actual newest activity timestamp synced (not 'now')
    # This ensures we only fetch new activities on the next sync, not refetch existing ones
    newest_activity_time: datetime | None = None
    if all_activities_timestamps:
        # Find the newest activity start_date from the fetched activities
        newest_activity_time = max(all_activities_timestamps)
        # Ensure timezone-aware
        if newest_activity_time.tzinfo is None:
            newest_activity_time = newest_activity_time.replace(tzinfo=timezone.utc)
        logger.info(
            f"[SYNC] Newest activity synced: {newest_activity_time.isoformat()} "
            f"(from {total_fetched} activities fetched)"
        )

    # Clear timestamps from memory
    del all_activities_timestamps
    # Update last_sync_at to the actual newest activity timestamp (not 'now')
    # This ensures we don't refetch activities on the next sync
    # Only update if we actually synced activities, otherwise keep existing last_sync_at
    if newest_activity_time is not None:
        # Only update if this is newer than existing last_sync_at
        if account.last_sync_at is None or newest_activity_time > account.last_sync_at:
            account.last_sync_at = newest_activity_time
            logger.info(
                f"[SYNC] Updated last_sync_at to newest activity: {newest_activity_time.isoformat()}"
            )
        else:
            logger.debug(
                f"[SYNC] Keeping existing last_sync_at ({account.last_sync_at.isoformat()}) "
                f"as it's newer than synced activity ({newest_activity_time.isoformat()})"
            )
    elif imported_count > 0:
        # If we imported activities but couldn't determine newest (shouldn't happen), use now as fallback
        logger.warning(
            "[SYNC] Imported activities but couldn't determine newest timestamp, using 'now' as fallback"
        )
        account.last_sync_at = now
    # If no activities were imported, don't update last_sync_at (keep existing value)

    account.sync_success_count = (account.sync_success_count or 0) + 1
    account.last_sync_error = None

    # Final commit for account updates
    try:
        session.commit()
    except IntegrityError as e:
        # This should not happen now since we handle it per batch, but keep as safety net
        session.rollback()
        logger.warning(
            f"[SYNC] IntegrityError during final commit: {e}. "
            "This should have been handled per batch."
        )
        # Fallback: try to update account anyway
        try:
            account.last_sync_at = now
            account.sync_success_count = (account.sync_success_count or 0) + 1
            account.last_sync_error = None
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("[SYNC] Failed to update account after batch processing")

    logger.info(
        f"[SYNC] Sync complete for user_id={user_id}: "
        f"imported={imported_count}, skipped={skipped_count}, duplicates={duplicate_count}, "
        f"total_fetched={total_fetched}"
    )

    # Memory monitoring after sync
    try:
        from app.core.system_memory import log_memory_snapshot
        log_memory_snapshot("sync_end")
    except Exception:
        pass  # Don't fail if memory monitoring fails

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
        "total_fetched": total_fetched,
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


def _should_sync_user(account: StravaAccount, session, now: datetime) -> tuple[bool, str]:
    """Determine if a user should be synced based on last sync time and activity patterns.

    Args:
        account: StravaAccount object
        session: Database session
        now: Current datetime

    Returns:
        Tuple of (should_sync: bool, reason: str)
    """
    # Always sync if never synced before
    if not account.last_sync_at:
        return True, "First sync"

    time_since_sync = now - account.last_sync_at

    # Skip if synced very recently (within last 1 hour) - too soon for new activities
    if time_since_sync < timedelta(hours=1):
        return False, f"Synced {time_since_sync.total_seconds() / 3600:.1f} hours ago (too recent)"

    # Always sync if it's been more than 6 hours (scheduler runs every 6 hours)
    # This ensures we catch up even if user hasn't been active
    if time_since_sync >= timedelta(hours=6):
        return True, f"Last sync was {time_since_sync.total_seconds() / 3600:.1f} hours ago (scheduled sync)"

    # For 1-6 hours since last sync, check if user is active
    # If user has activities in the last 7 days, they're likely to have new activities
    recent_activity_date = now - timedelta(days=7)
    recent_activity = session.execute(
        select(Activity)
        .where(
            Activity.user_id == account.user_id,
            Activity.starts_at >= recent_activity_date,
        )
        .order_by(Activity.starts_at.desc())
        .limit(1)
    ).first()

    if recent_activity:
        # User is active - sync if it's been 2+ hours (reasonable time for new activity)
        if time_since_sync >= timedelta(hours=2):
            return True, f"Active user, last sync {time_since_sync.total_seconds() / 3600:.1f} hours ago"
        return False, f"Active user, but synced {time_since_sync.total_seconds() / 3600:.1f} hours ago (too soon)"
    # User hasn't been active recently - only sync if it's been 4+ hours
    # This reduces unnecessary syncs for inactive users
    if time_since_sync >= timedelta(hours=4):
        return True, f"Inactive user, last sync {time_since_sync.total_seconds() / 3600:.1f} hours ago (checking for new activities)"
    return False, f"Inactive user, synced {time_since_sync.total_seconds() / 3600:.1f} hours ago (unlikely to have new activities)"


def sync_all_users() -> dict[str, int | list[dict[str, int | str]]]:
    """Sync activities for all users with Strava accounts.

    Only syncs users when:
    - Never synced before (first sync)
    - Last sync was 6+ hours ago (scheduled sync)
    - Active users: last sync was 2+ hours ago
    - Inactive users: last sync was 4+ hours ago

    This reduces unnecessary API calls while ensuring active users stay up-to-date.

    Returns:
        Dictionary with total users synced and results per user
    """
    logger.info("[SYNC] Starting sync for all users")

    now = datetime.now(timezone.utc)

    with get_session() as session:
        accounts = session.execute(select(StravaAccount)).all()

        if not accounts:
            logger.info("[SYNC] No Strava accounts found to sync")
            return {"total_users": 0, "results": []}

        logger.info(f"[SYNC] Found {len(accounts)} user(s) to sync")

        results = []
        skipped_count = 0
        for account_row in accounts:
            account = account_row[0]
            user_id = account.user_id

            # Check if user should be synced
            should_sync, reason = _should_sync_user(account, session, now)

            if not should_sync:
                logger.debug(f"[SYNC] Skipping user_id={user_id} - {reason}")
                skipped_count += 1
                results.append({
                    "skipped": True,
                    "reason": reason,
                    "user_id": user_id,
                })
                continue

            logger.info(f"[SYNC] Syncing user_id={user_id} - {reason}")
            try:
                result = sync_user_activities(user_id)
                results.append(result)
            except Exception as e:
                logger.exception(f"[SYNC] Failed to sync user_id={user_id}: {e!s}")
                results.append({"error": f"Failed to sync: {e!s}", "user_id": user_id})

        successful = sum(1 for r in results if "error" not in r and r.get("skipped") is not True)
        logger.info(
            f"[SYNC] Sync complete: {successful}/{len(accounts)} users synced successfully, "
            f"{skipped_count} skipped (no new activities expected)"
        )

        return {
            "total_users": len(accounts),
            "successful": successful,
            "skipped": skipped_count,
            "failed": len(accounts) - successful - skipped_count,
            "results": results,
        }


def _build_athlete_thresholds(user_settings: UserSettings | None) -> AthleteThresholds | None:
    """Build AthleteThresholds from UserSettings.

    Args:
        user_settings: User settings with threshold configuration

    Returns:
        AthleteThresholds instance or None if no user settings
    """
    if not user_settings:
        return None

    return AthleteThresholds(
        ftp_watts=user_settings.ftp_watts,
        threshold_pace_ms=user_settings.threshold_pace_ms,
    )
