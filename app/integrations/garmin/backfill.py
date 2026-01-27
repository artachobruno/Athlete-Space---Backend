"""Backfill logic for Garmin activities.

Bounded, idempotent, rate-limit safe backfill.
- Fetches activity summaries only (no samples)
- Deduplicates via (source_provider, external_activity_id)
- Detects Strava duplicates (same start_time ± 2min, same distance ± 1%)
- Stops early on high overlap (>70%)
- Respects rate limits
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import Activity, UserIntegration
from app.db.session import get_session
from app.integrations.garmin.client import GarminClient, get_garmin_client
from app.integrations.garmin.normalize import normalize_garmin_activity
from app.workouts.workout_factory import WorkoutFactory


def backfill_garmin_activities(
    user_id: str,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Backfill Garmin activities for a user (bounded, idempotent, rate-limit safe).

    Rules:
    - Bounded: max(GARMIN_BACKFILL_DAYS, 90) days
    - Fetches activity summaries only (no samples)
    - Deduplicates via (source_provider, external_activity_id)
    - Detects Strava duplicates (same start_time ± 2min, same distance ± 1%)
    - Stops early if >70% overlap detected
    - Respects rate limits (sleep between pages)

    Args:
        user_id: User ID to backfill for
        from_date: Start date for backfill (default: GARMIN_BACKFILL_DAYS ago, max 90)
        to_date: End date for backfill (default: now)
        force: If True, force backfill even if recently completed

    Returns:
        Dict with backfill results: {ingested_count, skipped_count, error_count, status}
    """
    logger.info(f"[GARMIN_BACKFILL] Starting backfill for user_id={user_id}")

    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_BACKFILL] Garmin integration disabled, skipping backfill for user_id={user_id}")
        return {"ingested_count": 0, "skipped_count": 0, "error_count": 0, "status": "disabled"}

    # Bounded backfill: min(GARMIN_BACKFILL_DAYS, 90)
    backfill_days = min(settings.garmin_backfill_days, 90)
    if from_date is None:
        from_date = datetime.now(timezone.utc) - timedelta(days=backfill_days)
    if to_date is None:
        to_date = datetime.now(timezone.utc)

    logger.info(f"[GARMIN_BACKFILL] Backfill window: {from_date.date()} to {to_date.date()} ({backfill_days} days)")

    with get_session() as session:
        # Get user's Garmin integration
        integration = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "garmin",
                UserIntegration.revoked_at.is_(None),
            )
        ).first()

        if not integration:
            logger.warning(f"[GARMIN_BACKFILL] No active Garmin integration for user_id={user_id}")
            return {"ingested_count": 0, "skipped_count": 0, "error_count": 0, "status": "no_integration"}

        integration_obj = integration[0]

        # Backfill concurrency lock: Check if backfill already completed recently
        # Use last_sync_at as a simple lock (if synced within last hour, skip unless forced)
        if not force and integration_obj.last_sync_at:
            time_since_sync = datetime.now(timezone.utc) - integration_obj.last_sync_at
            if time_since_sync < timedelta(hours=1):
                logger.info(
                    f"[GARMIN_BACKFILL] Backfill completed recently ({time_since_sync}), "
                    f"skipping to prevent duplicate backfills. Use force=True to override."
                )
                return {
                    "ingested_count": 0,
                    "skipped_count": 0,
                    "error_count": 0,
                    "status": "skipped_recent_sync",
                    "last_sync_at": integration_obj.last_sync_at.isoformat(),
                }

        try:
            # Get Garmin client
            client = get_garmin_client(user_id)
        except ValueError as e:
            logger.error(f"[GARMIN_BACKFILL] Failed to get Garmin client: {e}")
            return {"ingested_count": 0, "skipped_count": 0, "error_count": 0, "status": "client_error", "error": str(e)}

        # Backfill stats
        stats: dict[str, int] = {
            "ingested_count": 0,
            "skipped_count": 0,
            "error_count": 0,
            "duplicate_count": 0,
            "strava_duplicate_count": 0,
            "total_fetched": 0,
        }
        max_pages = 50  # Safety limit: max 50 pages (5000 activities)

        # Split large date ranges into smaller chunks to avoid API 400 errors
        # Garmin API may have limits on date range size, so we'll use 30-day chunks
        chunk_days = 30
        date_range_days = (to_date - from_date).days
        use_chunks = date_range_days > chunk_days

        try:
            if use_chunks:
                _process_backfill_in_chunks(
                    session, client, user_id, from_date, to_date, chunk_days, date_range_days, max_pages, stats
                )
            else:
                _process_date_range_chunk(session, client, user_id, from_date, to_date, max_pages, stats)

        except Exception as e:
            logger.exception(f"[GARMIN_BACKFILL] Backfill failed: {e}")
            stats["error_count"] += 1

        ingested_count = stats["ingested_count"]
        skipped_count = stats["skipped_count"]
        error_count = stats["error_count"]
        duplicate_count = stats["duplicate_count"]
        strava_duplicate_count = stats["strava_duplicate_count"]
        total_fetched = stats["total_fetched"]

        # Update last_sync_at
        integration_obj.last_sync_at = datetime.now(timezone.utc)

        # Set historical backfill cursor to the start of the backfill window
        # This marks how far back we've synced (for future historical backfill)
        if not integration_obj.historical_backfill_cursor_date:
            integration_obj.historical_backfill_cursor_date = from_date
            logger.info(
                f"[GARMIN_BACKFILL] Set historical_backfill_cursor_date to {from_date.date()} "
                f"for user_id={user_id}"
            )

        session.commit()

        logger.info(
            f"[GARMIN_BACKFILL] Backfill complete for user_id={user_id}: "
            f"ingested={ingested_count}, skipped={skipped_count} "
            f"(duplicates={duplicate_count}, strava_duplicates={strava_duplicate_count}), "
            f"errors={error_count}, total_fetched={total_fetched}"
        )

        return {
            "ingested_count": ingested_count,
            "skipped_count": skipped_count,
            "duplicate_count": duplicate_count,
            "strava_duplicate_count": strava_duplicate_count,
            "error_count": error_count,
            "total_fetched": total_fetched,
            "status": "completed",
        }


def _process_date_range_chunk(
    session: Session,
    client: GarminClient,
    user_id: str,
    start_date: datetime,
    end_date: datetime,
    max_pages: int,
    stats: dict[str, int],
) -> None:
    """Process a single date range chunk of activities.

    Args:
        session: Database session
        client: Garmin client instance
        user_id: User ID
        start_date: Start date for this chunk
        end_date: End date for this chunk
        max_pages: Maximum pages to process
        stats: Dictionary with stats to update (total_fetched, ingested_count, etc.)
    """
    for page_num, activities_page in enumerate(
        client.yield_activity_summaries(
            start_date=start_date,
            end_date=end_date,
            per_page=100,
            max_pages=max_pages,
            sleep_seconds=0.5,  # Rate limit safety
        )
    ):
        if page_num >= max_pages:
            logger.warning(f"[GARMIN_BACKFILL] Reached max pages limit ({max_pages}), stopping")
            break

        stats["total_fetched"] += len(activities_page)

        # Process each activity
        page_ingested, page_skipped, page_errors, page_duplicates, page_strava = (
            _process_backfill_activities_page(session, user_id, activities_page)
        )
        stats["ingested_count"] += page_ingested
        stats["skipped_count"] += page_skipped
        stats["error_count"] += page_errors
        stats["duplicate_count"] += page_duplicates
        stats["strava_duplicate_count"] += page_strava

        # Commit after each page to avoid memory bloat
        session.commit()

        # Check for early exit: >70% overlap
        if _should_stop_early(stats):
            logger.info(
                "[GARMIN_BACKFILL] High overlap detected, "
                "stopping early to avoid unnecessary API calls"
            )
            break


def _process_backfill_in_chunks(
    session: Session,
    client: GarminClient,
    user_id: str,
    from_date: datetime,
    to_date: datetime,
    chunk_days: int,
    date_range_days: int,
    max_pages: int,
    stats: dict[str, int],
) -> None:
    """Process backfill in smaller date range chunks.

    Args:
        session: Database session
        client: Garmin client instance
        user_id: User ID
        from_date: Start date for backfill
        to_date: End date for backfill
        chunk_days: Number of days per chunk
        date_range_days: Total days in date range
        max_pages: Maximum pages to process per chunk
        stats: Dictionary with stats to update
    """
    logger.info(
        f"[GARMIN_BACKFILL] Large date range ({date_range_days} days), "
        f"splitting into {chunk_days}-day chunks"
    )
    current_start = from_date
    chunk_num = 0

    while current_start < to_date:
        chunk_num += 1
        current_end = min(current_start + timedelta(days=chunk_days), to_date)
        logger.info(
            f"[GARMIN_BACKFILL] Processing chunk {chunk_num}: "
            f"{current_start.date()} to {current_end.date()}"
        )

        try:
            _process_date_range_chunk(session, client, user_id, current_start, current_end, max_pages, stats)
            # Move to next chunk
            current_start = current_end

            # Small delay between chunks to respect rate limits
            if current_start < to_date:
                time.sleep(1.0)

        except Exception as chunk_error:
            logger.error(
                f"[GARMIN_BACKFILL] Error processing chunk {chunk_num} "
                f"({current_start.date()} to {current_end.date()}): {chunk_error}"
            )
            stats["error_count"] += 1
            # Continue with next chunk instead of failing completely
            current_start = current_end
            continue


def _should_stop_early(stats: dict[str, int]) -> bool:
    """Check if we should stop early due to high overlap rate.

    Args:
        stats: Dictionary with backfill statistics

    Returns:
        True if overlap rate > 70% and we have enough data to check
    """
    if stats["total_fetched"] <= 10:
        return False

    overlap_rate = (stats["duplicate_count"] + stats["strava_duplicate_count"]) / stats["total_fetched"]
    return overlap_rate > 0.7


def _process_backfill_activities_page(
    session: Session,
    user_id: str,
    activities_page: list[dict[str, Any]],
) -> tuple[int, int, int, int, int]:
    """Process a page of activities for backfill.

    Returns:
        Tuple of (ingested_count, skipped_count, error_count, duplicate_count, strava_duplicate_count)
    """
    ingested_count = 0
    skipped_count = 0
    error_count = 0
    duplicate_count = 0
    strava_duplicate_count = 0

    for activity_item in activities_page:
        activity_payload: dict[str, Any] = activity_item
        try:
            result = _process_activity_for_backfill(
                session=session,
                user_id=user_id,
                activity_payload=activity_payload,
            )

            if result == "ingested":
                ingested_count += 1
            elif result == "skipped_duplicate":
                skipped_count += 1
                duplicate_count += 1
            elif result == "skipped_strava_duplicate":
                skipped_count += 1
                strava_duplicate_count += 1
            else:
                error_count += 1

        except Exception as e:
            logger.exception(f"[GARMIN_BACKFILL] Error processing activity: {e}")
            error_count += 1

    return ingested_count, skipped_count, error_count, duplicate_count, strava_duplicate_count


def check_garmin_activity_exists(
    session: Session,
    external_activity_id: str,
) -> Activity | None:
    """Check if Garmin activity already exists.

    Args:
        session: Database session
        external_activity_id: Garmin activity ID

    Returns:
        Existing Activity if found, None otherwise
    """
    existing = session.execute(
        select(Activity).where(
            Activity.source_provider == "garmin",
            Activity.external_activity_id == external_activity_id,
        )
    ).first()

    return existing[0] if existing else None


def check_strava_duplicate(
    session: Session,
    user_id: str,
    start_time: datetime,
    distance_meters: float | None,
) -> Activity | None:
    """Check if a Strava activity exists that matches this Garmin activity.

    Duplicate criteria:
    - Same start_time ± 2 minutes
    - Same distance ± 1% (if distance available)

    Args:
        session: Database session
        user_id: User ID
        start_time: Activity start time
        distance_meters: Activity distance in meters (optional)

    Returns:
        Matching Strava Activity if found, None otherwise
    """
    # Time window: ± 2 minutes
    time_window_start = start_time - timedelta(seconds=120)
    time_window_end = start_time + timedelta(seconds=120)

    query = select(Activity).where(
        Activity.user_id == user_id,
        Activity.source == "strava",
        Activity.starts_at >= time_window_start,
        Activity.starts_at <= time_window_end,
    )

    # If distance available, check ± 1%
    if distance_meters is not None and distance_meters > 0:
        distance_tolerance = distance_meters * 0.01  # 1%
        query = query.where(
            Activity.distance_meters.is_not(None),
            Activity.distance_meters >= distance_meters - distance_tolerance,
            Activity.distance_meters <= distance_meters + distance_tolerance,
        )

    existing = session.execute(query).first()
    return existing[0] if existing else None


def check_garmin_duplicate(
    session: Session,
    user_id: str,
    start_time: datetime,
    distance_meters: float | None,
) -> Activity | None:
    """Check if a Garmin activity exists that matches this Strava activity.

    Duplicate criteria:
    - Same start_time ± 2 minutes
    - Same distance ± 1% (if distance available)

    Args:
        session: Database session
        user_id: User ID
        start_time: Activity start time
        distance_meters: Activity distance in meters (optional)

    Returns:
        Matching Garmin Activity if found, None otherwise
    """
    # Time window: ± 2 minutes
    time_window_start = start_time - timedelta(seconds=120)
    time_window_end = start_time + timedelta(seconds=120)

    query = select(Activity).where(
        Activity.user_id == user_id,
        Activity.source_provider == "garmin",
        Activity.starts_at >= time_window_start,
        Activity.starts_at <= time_window_end,
    )

    # If distance available, check ± 1%
    if distance_meters is not None and distance_meters > 0:
        distance_tolerance = distance_meters * 0.01  # 1%
        query = query.where(
            Activity.distance_meters.is_not(None),
            Activity.distance_meters >= distance_meters - distance_tolerance,
            Activity.distance_meters <= distance_meters + distance_tolerance,
        )

    existing = session.execute(query).first()
    return existing[0] if existing else None


def _process_activity_for_backfill(
    session: Session,
    user_id: str,
    activity_payload: dict[str, Any],
) -> str:
    """Process a single Garmin activity for backfill.

    Args:
        session: Database session
        user_id: User ID
        activity_payload: Raw Garmin activity payload

    Returns:
        "ingested", "skipped_duplicate", "skipped_strava_duplicate", or "error"
    """
    try:
        # Normalize activity
        normalized = normalize_garmin_activity(activity_payload)
        external_activity_id = normalized.get("external_activity_id")

        if not external_activity_id:
            logger.warning("[GARMIN_BACKFILL] Activity missing external_activity_id, skipping")
            return "error"

        # Check if Garmin activity already exists
        existing_garmin = check_garmin_activity_exists(session, external_activity_id)
        if existing_garmin:
            logger.debug(f"[GARMIN_BACKFILL] Garmin activity already exists: {external_activity_id}")
            return "skipped_duplicate"

        # Check for Strava duplicate
        start_time = datetime.fromisoformat(normalized["start_time"].replace("Z", "+00:00"))
        distance_meters = normalized.get("distance_meters")
        existing_strava = check_strava_duplicate(session, user_id, start_time, distance_meters)

        if existing_strava:
            logger.info(
                f"[GARMIN_BACKFILL] Strava duplicate detected for Garmin activity {external_activity_id}: "
                f"strava_id={existing_strava.source_activity_id}, "
                f"start_time_diff={abs((start_time - existing_strava.starts_at).total_seconds())}s"
            )
            # Link Garmin data to Strava activity (store Garmin ID in metrics for reference)
            if existing_strava.metrics and isinstance(existing_strava.metrics, dict):
                existing_strava.metrics["garmin_activity_id"] = external_activity_id
                session.commit()
            return "skipped_strava_duplicate"

        # Create new activity
        activity = Activity(
            user_id=user_id,
            source="garmin",
            source_activity_id=external_activity_id,
            source_provider="garmin",
            external_activity_id=external_activity_id,
            sport=normalized.get("sport", "other"),
            starts_at=start_time,
            ends_at=datetime.fromisoformat(normalized["ends_at"].replace("Z", "+00:00")) if normalized.get("ends_at") else None,
            duration_seconds=normalized.get("duration_seconds", 0),
            distance_meters=normalized.get("distance_meters"),
            elevation_gain_meters=normalized.get("elevation_gain_meters"),
            calories=normalized.get("calories"),
            title=normalized.get("title"),
            metrics=normalized.get("metrics", {}),
        )

        session.add(activity)
        session.flush()  # Ensure ID is generated

        # PHASE 3: Enforce workout + execution creation (mandatory invariant)
        workout = WorkoutFactory.get_or_create_for_activity(session, activity)
        WorkoutFactory.attach_activity(session, workout, activity)

        logger.debug(f"[GARMIN_BACKFILL] Stored activity with workout and execution: {external_activity_id}")
    except IntegrityError:
        # Race condition: activity was inserted between check and commit
        session.rollback()
        logger.debug("[GARMIN_BACKFILL] Duplicate detected during commit (race condition)")
        return "skipped_duplicate"
    except Exception as e:
        logger.exception(f"[GARMIN_BACKFILL] Error processing activity: {e}")
        session.rollback()
        return "error"
    else:
        return "ingested"
