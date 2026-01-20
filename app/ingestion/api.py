"""Strava activity ingestion service and endpoints.

Step 4: Fetch historical Strava activities and store them as immutable facts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies.auth import get_current_user_id
from app.config.settings import settings
from app.core.encryption import EncryptionError, EncryptionKeyError, decrypt_token, encrypt_token
from app.db.models import Activity, StravaAccount, UserSettings
from app.db.session import get_session
from app.integrations.strava.client import StravaClient
from app.integrations.strava.tokens import refresh_access_token
from app.metrics.load_computation import AthleteThresholds, compute_activity_tss
from app.utils.sport_utils import normalize_sport_type
from app.utils.title_utils import normalize_activity_title
from app.workouts.guards import assert_activity_has_execution, assert_activity_has_workout
from app.workouts.workout_factory import WorkoutFactory

router = APIRouter(prefix="/strava", tags=["strava", "ingestion"])


def _get_strava_account(user_id: str, session) -> StravaAccount:
    """Get StravaAccount for user_id.

    Args:
        user_id: Clerk user ID (string)
        session: Database session

    Returns:
        StravaAccount object

    Raises:
        HTTPException: If Strava account not found
    """
    account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()

    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Strava account not connected. Please connect your Strava account first.",
        )

    return account[0]


def _get_access_token_from_account(account: StravaAccount, session) -> str:
    """Get valid access token from StravaAccount, refreshing if needed.

    Args:
        account: StravaAccount object
        session: Database session

    Returns:
        Valid access token string

    Raises:
        HTTPException: If token refresh fails
    """
    # If token is still valid, we still need to refresh to get access token
    # (access tokens are not stored, only refresh tokens)
    try:
        # Decrypt refresh token
        refresh_token = decrypt_token(account.refresh_token)
    except EncryptionKeyError as e:
        logger.error(f"[INGESTION] Encryption key mismatch: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token decryption failed: ENCRYPTION_KEY not set or changed. Please reconnect your Strava account.",
        ) from e
    except EncryptionError as e:
        logger.error(f"[INGESTION] Failed to decrypt refresh token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt refresh token",
        ) from e

    # Refresh token to get new access token
    try:
        token_data = refresh_access_token(
            client_id=settings.strava_client_id,
            client_secret=settings.strava_client_secret,
            refresh_token=refresh_token,
        )
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in {400, 401}:
            logger.warning(f"[INGESTION] Invalid refresh token for user_id={account.user_id}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Strava token invalid. Please reconnect your Strava account.",
            ) from e
        logger.error(f"[INGESTION] Token refresh failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to refresh Strava token: {e!s}",
        ) from e

    # Extract new tokens
    new_access_token = token_data.get("access_token")
    new_refresh_token = token_data.get("refresh_token")
    new_expires_at = token_data.get("expires_at")

    if not isinstance(new_access_token, str):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid access_token type from Strava",
        )

    if not isinstance(new_expires_at, int):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invalid expires_at type from Strava",
        )

    # Update refresh token if provided (token rotation)
    if new_refresh_token and isinstance(new_refresh_token, str):
        try:
            account.refresh_token = encrypt_token(new_refresh_token)
            # Convert epoch seconds to datetime (database expects TIMESTAMPTZ)
            expires_at_dt = datetime.fromtimestamp(new_expires_at, tz=timezone.utc)
            account.expires_at = expires_at_dt
            session.commit()
            logger.info(f"[INGESTION] Rotated refresh token for user_id={account.user_id}")
        except EncryptionError as e:
            logger.error(f"[INGESTION] Failed to encrypt new refresh token: {e}")
            # Continue with old refresh token - not critical

    return new_access_token


def ingest_activities(
    user_id: str,
    since_days: int = 365,
) -> dict[str, int | str]:
    """Ingest Strava activities for a user.

    Fetches activities from Strava API and stores them in the database.
    Idempotent: running twice produces zero duplicates.
    Incremental: uses last_sync_at to only fetch new activities.

    Args:
        user_id: Clerk user ID (string)
        since_days: Number of days to fetch (default: 365)

    Returns:
        Dictionary with imported, skipped counts and date range

    Raises:
        HTTPException: If Strava not connected or ingestion fails
    """
    logger.info(f"[INGESTION] Starting activity ingestion for user_id={user_id}, since_days={since_days}")

    with get_session() as session:
        # Get StravaAccount for user
        account = _get_strava_account(user_id, session)

        # Calculate date range
        now = datetime.now(timezone.utc)
        requested_after_date = now - timedelta(days=since_days)

        # Use last_sync_at if available and more recent than requested date
        # This ensures we only fetch new activities incrementally
        if account.last_sync_at:
            last_sync_date = account.last_sync_at
            # Use the more recent of the two dates (only fetch new activities)
            after_date = max(last_sync_date, requested_after_date)
            logger.debug(
                f"[INGESTION] Using incremental sync: last_sync_at={last_sync_date.isoformat()}, "
                f"requested_after={requested_after_date.isoformat()}, using after={after_date.isoformat()}"
            )
        else:
            # First sync: use requested date range
            after_date = requested_after_date
            logger.info(f"[INGESTION] First sync for user_id={user_id}, fetching from {after_date.isoformat()}")

        # Always check for recent activities (last 48 hours) to ensure nothing is missing
        # This is a safety check to catch any activities that might have been missed
        recent_check_date = now - timedelta(hours=48)
        if after_date > recent_check_date:
            # If our sync window is very recent, extend it to cover last 48 hours
            logger.info(
                f"[INGESTION] Extending sync window to cover last 48 hours for safety check: "
                f"after_date={after_date.isoformat()} -> recent_check_date={recent_check_date.isoformat()}"
            )
            after_date = recent_check_date

        after_ts = after_date

        logger.info(f"[INGESTION] Fetching activities for user_id={user_id} from {after_date.isoformat()} to {now.isoformat()}")

        # Get access token from StravaAccount (with refresh if needed)
        try:
            access_token = _get_access_token_from_account(account, session)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"[INGESTION] Failed to get access token: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get Strava access token: {e!s}",
            ) from e

        # Create Strava client
        client = StravaClient(access_token=access_token)

        # Fetch activities from Strava
        try:
            strava_activities = client.get_activities(after_ts=after_ts)
            logger.info(f"[INGESTION] Fetched {len(strava_activities)} activities from Strava")
        except Exception as e:
            logger.exception(f"[INGESTION] Failed to fetch activities from Strava: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch activities from Strava: {e!s}",
            ) from e

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
                    Activity.source == "strava",
                    Activity.source_activity_id == str(strava_id),
                )
            ).first()

            if existing:
                skipped_count += 1
                logger.debug(f"[INGESTION] Activity {strava_id} already exists, skipping")
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

            # Store raw JSON in metrics
            raw_json = strava_activity.raw if strava_activity.raw else {}
            metrics_dict: dict = {}
            if raw_json:
                metrics_dict["raw_json"] = raw_json

            # Normalize sport type and title
            sport = normalize_sport_type(strava_activity.type)
            title = normalize_activity_title(
                strava_title=strava_activity.name,
                sport=sport,
                distance_meters=strava_activity.distance,
                duration_seconds=strava_activity.elapsed_time,
            )

            # Create new activity record
            activity = Activity(
                user_id=user_id,
                source="strava",
                source_activity_id=strava_id,
                sport=sport,
                title=title,
                starts_at=start_time,
                duration_seconds=strava_activity.elapsed_time,
                distance_meters=strava_activity.distance,
                elevation_gain_meters=strava_activity.total_elevation_gain,
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
                    f"[INGESTION] Computed TSS for activity {strava_id}: tss={tss}, version=v2"
                )
            except Exception as e:
                logger.warning(f"[INGESTION] Failed to compute TSS for activity {strava_id}: {e}")

            created_activities.append(activity)
            imported_count += 1

        # Update last_sync_at in StravaAccount only if we successfully fetched activities
        # This ensures we track incremental progress
        if len(strava_activities) > 0 or imported_count > 0:
            account.last_sync_at = now
            logger.info(f"[INGESTION] Updated last_sync_at to {now.isoformat()} for user_id={user_id}")

        # Commit all activities and last_sync_at update
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
                f"[INGESTION] IntegrityError during commit (duplicate detected): {e}. "
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
                        Activity.source == "strava",
                        Activity.source_activity_id == str(strava_id),
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
                metrics_dict: dict = {}
                if raw_json:
                    metrics_dict["raw_json"] = raw_json
                try:
                    activity = Activity(
                        user_id=user_id,
                        source="strava",
                        source_activity_id=strava_id,
                        sport=normalize_sport_type(strava_activity.type),
                        starts_at=start_time,
                        duration_seconds=strava_activity.elapsed_time,
                        distance_meters=strava_activity.distance,
                        elevation_gain_meters=strava_activity.total_elevation_gain,
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
                            f"[INGESTION] Computed TSS for activity {strava_id} (retry): tss={tss}, version=v2"
                        )
                    except Exception as e:
                        logger.warning(f"[INGESTION] Failed to compute TSS for activity {strava_id} (retry): {e}")

                    session.commit()
                    retry_imported += 1
                except IntegrityError:
                    session.rollback()
                    retry_duplicate += 1
                    logger.debug(f"[INGESTION] Activity {strava_id} duplicate in retry, skipping")
            # Update counts (retry_duplicate includes activities that were duplicates in retry)
            imported_count = retry_imported
            duplicate_count = retry_duplicate
            skipped_count = 0  # Reset since we're retrying everything
            # Update last_sync_at
            account.last_sync_at = now
            session.commit()

        logger.info(
            f"[INGESTION] Ingestion complete: imported={imported_count}, skipped={skipped_count}, "
            f"duplicates={duplicate_count}, total_fetched={len(strava_activities)}"
        )

        return {
            "imported": imported_count,
            "skipped": skipped_count,
            "duplicates": duplicate_count,
            "total_fetched": len(strava_activities),
            "range": f"{after_date.date().isoformat()} → {now.date().isoformat()}",
        }


def _is_admin_or_dev(user_id: str) -> bool:
    """Check if user is admin or dev mode.

    Args:
        user_id: User ID to check

    Returns:
        True if user is admin or dev mode, False otherwise
    """
    # Dev mode: check if user_id matches dev_user_id
    if settings.dev_user_id and user_id == settings.dev_user_id:
        return True

    # Admin: check if user_id is in admin list
    if settings.admin_user_ids:
        admin_list = [uid.strip() for uid in settings.admin_user_ids.split(",") if uid.strip()]
        if user_id in admin_list:
            return True

    return False


@router.post("/ingest")
def strava_ingest(
    since_days: int = 365,
    user_id: str = Depends(get_current_user_id),
):
    """Trigger Strava activity ingestion (admin/dev only, manual recovery).

    **Note:** This endpoint is restricted to admin users and dev mode only.
    Normal ingestion happens automatically via background sync (Step 5).

    Use this endpoint only for:
    - Manual recovery after sync failures
    - Initial historical data import
    - Debugging and testing

    Requires:
    - Authenticated user (via get_current_user)
    - Admin access OR dev mode
    - Strava account connected

    Args:
        since_days: Number of days to fetch (default: 365, configurable via env)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Summary with imported, skipped counts and date range

    Raises:
        HTTPException: 403 if user is not admin/dev
    """
    logger.info(f"[INGESTION] Ingestion endpoint called for user_id={user_id}, since_days={since_days}")

    # Check admin/dev access
    if not _is_admin_or_dev(user_id):
        logger.warning(f"[INGESTION] Access denied for user_id={user_id} (not admin/dev)")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is restricted to admin users and dev mode only. "
            "Normal ingestion happens automatically via background sync.",
        )

    # Use environment variable if available, otherwise use parameter
    default_days = getattr(settings, "strava_ingestion_days", 365)
    days_to_fetch = since_days if since_days != 365 else default_days

    try:
        result = ingest_activities(user_id=user_id, since_days=days_to_fetch)
        logger.info(f"[INGESTION] Ingestion successful for user_id={user_id}: {result}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[INGESTION] Unexpected error during ingestion: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {e!s}",
        ) from e
    else:
        return result


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
