"""Backfill logic for Garmin activities.

Uses Summary Backfill API (async, webhook-driven):
- Triggers Summary Backfill requests (30-day chunks)
- Updates database state (garmin_history_requested_at, garmin_history_complete)
- Does NOT fetch activities (data arrives via webhooks)
- Idempotent (duplicate requests return HTTP 409, ignored)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.config.settings import settings
from app.db.models import Activity, UserIntegration
from app.db.session import get_session
from app.integrations.garmin.summary_backfill import trigger_full_history_backfill


def backfill_garmin_activities(
    user_id: str,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    force: bool = False,
) -> dict[str, int | str]:
    """Trigger Garmin Summary Backfill for a user (async, webhook-driven).

    Rules:
    - Triggers Summary Backfill API requests (30-day chunks)
    - Updates database state (garmin_history_requested_at, garmin_history_complete)
    - Does NOT fetch activities (data arrives via webhooks)
    - Idempotent (duplicate requests return HTTP 409, ignored)
    - Bounded: max(GARMIN_BACKFILL_DAYS, 90) days

    Args:
        user_id: User ID to backfill for
        from_date: Start date for backfill (default: GARMIN_BACKFILL_DAYS ago, max 90)
        to_date: End date for backfill (default: now)
        force: If True, force backfill even if recently requested

    Returns:
        Dict with backfill results: {total_requests, accepted_count, duplicate_count, error_count, status}
    """
    logger.info(f"[GARMIN_BACKFILL] Starting summary backfill trigger for user_id={user_id}")

    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_BACKFILL] Garmin integration disabled, skipping backfill for user_id={user_id}")
        return {"total_requests": 0, "accepted_count": 0, "duplicate_count": 0, "error_count": 0, "status": "disabled"}

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
            return {"total_requests": 0, "accepted_count": 0, "duplicate_count": 0, "error_count": 0, "status": "no_integration"}

        integration_obj = integration[0]

        # Check if backfill already requested recently (unless forced)
        if not force and integration_obj.garmin_history_requested_at:
            time_since_request = datetime.now(timezone.utc) - integration_obj.garmin_history_requested_at
            if time_since_request < timedelta(hours=1):
                logger.info(
                    f"[GARMIN_BACKFILL] Backfill requested recently ({time_since_request}), "
                    f"skipping to prevent duplicate requests. Use force=True to override."
                )
                return {
                    "total_requests": 0,
                    "accepted_count": 0,
                    "duplicate_count": 0,
                    "error_count": 0,
                    "status": "skipped_recent_request",
                    "requested_at": integration_obj.garmin_history_requested_at.isoformat(),
                }

        # Trigger summary backfill (chunks into 30-day windows)
        try:
            result = trigger_full_history_backfill(
                user_id=user_id,
                start=from_date,
                end=to_date,
            )

            # Update database state
            integration_obj.garmin_history_requested_at = datetime.now(timezone.utc)
            integration_obj.garmin_history_complete = False

            session.commit()

            logger.info(
                f"[GARMIN_BACKFILL] Summary backfill triggered for user_id={user_id}: "
                f"total_requests={result['total_requests']}, "
                f"accepted={result['accepted_count']}, "
                f"duplicates={result['duplicate_count']}, "
                f"errors={result['error_count']}"
            )

            # Extract int values (results key contains list, but we only need the counts)
            total_requests_val = result["total_requests"]
            accepted_count_val = result["accepted_count"]
            duplicate_count_val = result["duplicate_count"]
            error_count_val = result["error_count"]

            # Type narrowing: these keys are always ints, not lists
            if not isinstance(total_requests_val, int):
                raise TypeError(f"Expected int for total_requests, got {type(total_requests_val)}")
            if not isinstance(accepted_count_val, int):
                raise TypeError(f"Expected int for accepted_count, got {type(accepted_count_val)}")
            if not isinstance(duplicate_count_val, int):
                raise TypeError(f"Expected int for duplicate_count, got {type(duplicate_count_val)}")
            if not isinstance(error_count_val, int):
                raise TypeError(f"Expected int for error_count, got {type(error_count_val)}")

            return {
                "total_requests": total_requests_val,
                "accepted_count": accepted_count_val,
                "duplicate_count": duplicate_count_val,
                "error_count": error_count_val,
                "status": "completed",
            }

        except Exception as e:
            logger.exception(f"[GARMIN_BACKFILL] Summary backfill failed: {e}")
            return {
                "total_requests": 0,
                "accepted_count": 0,
                "duplicate_count": 0,
                "error_count": 1,
                "status": "error",
                "error": str(e),
            }


def check_garmin_activity_exists(
    session,
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


def check_garmin_duplicate(
    session,
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
        Activity.source == "garmin",
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


def check_strava_duplicate(
    session,
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
