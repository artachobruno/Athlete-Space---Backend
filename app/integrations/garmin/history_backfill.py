"""DEPRECATED: Pull-based historical backfill for Garmin activities.

DO NOT USE. Garmin is not a pull API. This module used yield_activity_summaries
(polling /activities) which is disabled.

Use Summary Backfill (summary_backfill.py) + webhooks instead. Trigger backfill,
then ingest from webhook payloads. See app/integrations/garmin/README.md.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import Activity, User, UserIntegration
from app.db.session import get_session
from app.integrations.garmin.backfill import check_garmin_activity_exists, check_strava_duplicate
from app.integrations.garmin.client import get_garmin_client
from app.integrations.garmin.normalize import normalize_garmin_activity
from app.workouts.workout_factory import WorkoutFactory


def _process_history_activity(
    session: Session,
    user_id: str,
    activity_payload: dict[str, Any],
) -> tuple[str, int, int, int]:
    """Process a single activity for history backfill.

    Args:
        session: Database session
        user_id: User ID
        activity_payload: Raw activity payload

    Returns:
        Tuple of (result, ingested, skipped, error) counts
    """
    try:
        # Use the same processing logic as regular backfill
        normalized = normalize_garmin_activity(activity_payload)
        external_activity_id = normalized.get("external_activity_id")

        if not external_activity_id:
            logger.warning("[GARMIN_HISTORY] Activity missing external_activity_id, skipping")
            return ("error", 0, 0, 1)

        # Check if Garmin activity already exists
        existing_garmin = check_garmin_activity_exists(session, external_activity_id)
        if existing_garmin:
            logger.debug(f"[GARMIN_HISTORY] Garmin activity already exists: {external_activity_id}")
            return ("skipped_duplicate", 0, 1, 0)

        # Check for Strava duplicate
        start_time = datetime.fromisoformat(normalized["start_time"].replace("Z", "+00:00"))
        distance_meters = normalized.get("distance_meters")
        existing_strava = check_strava_duplicate(session, user_id, start_time, distance_meters)

        if existing_strava:
            logger.info(
                f"[GARMIN_HISTORY] Strava duplicate detected for Garmin activity {external_activity_id}: "
                f"strava_id={existing_strava.source_activity_id}"
            )
            # Link Garmin data to Strava activity
            if existing_strava.metrics and isinstance(existing_strava.metrics, dict):
                existing_strava.metrics["garmin_activity_id"] = external_activity_id
                session.commit()
            return ("skipped_strava_duplicate", 0, 1, 0)

        # Create new activity
        activity = Activity(
            user_id=user_id,
            source="garmin",
            source_activity_id=external_activity_id,
            source_provider="garmin",
            external_activity_id=external_activity_id,
            sport=normalized.get("sport", "other"),
            starts_at=start_time,
            ends_at=(
                datetime.fromisoformat(normalized["ends_at"].replace("Z", "+00:00"))
                if normalized.get("ends_at")
                else None
            ),
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
        # Note: get_or_create_for_activity already creates the execution, so no need to call attach_activity
        workout = WorkoutFactory.get_or_create_for_activity(session, activity)

        logger.debug(f"[GARMIN_HISTORY] Stored activity with workout and execution: {external_activity_id}")
    except IntegrityError as e:
        # Race condition: activity was inserted between check and commit
        session.rollback()
        logger.debug(f"[GARMIN_HISTORY] Duplicate detected during commit (race condition): {e}")
        return ("skipped_duplicate", 0, 1, 0)
    except Exception as e:
        logger.exception(
            f"[GARMIN_HISTORY] Error processing activity {external_activity_id if 'external_activity_id' in locals() else 'unknown'}: {e}",
            exc_info=True,
        )
        session.rollback()
        return ("error", 0, 0, 1)
    else:
        return ("ingested", 1, 0, 0)


def backfill_garmin_history_chunk(user_id: str) -> dict[str, Any]:
    """Process one chunk of historical backfill (90 days going backwards).

    Rules:
    - Reads cursor from database (historical_backfill_cursor_date)
    - Processes 90-day window: [cursor - 90 days, cursor)
    - Updates cursor after processing
    - Stops if no activities found or cursor < account creation date
    - Fetches activity summaries only (no samples)
    - Respects rate limits (sleep between pages, max pages per run)

    Args:
        user_id: User ID to backfill for

    Returns:
        Dict with backfill results: {
            ingested_count, skipped_count, error_count,
            cursor_date, status, complete
        }
    """
    logger.info(f"[GARMIN_HISTORY] Starting history chunk backfill for user_id={user_id}")

    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_HISTORY] Garmin integration disabled, skipping for user_id={user_id}")
        return {"ingested_count": 0, "skipped_count": 0, "error_count": 0, "status": "disabled"}

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
            logger.warning(f"[GARMIN_HISTORY] No active Garmin integration for user_id={user_id}")
            return {"ingested_count": 0, "skipped_count": 0, "error_count": 0, "status": "no_integration"}

        integration_obj = integration[0]

        # Check if already complete
        if integration_obj.historical_backfill_complete:
            logger.info(f"[GARMIN_HISTORY] Historical backfill already complete for user_id={user_id}")
            return {
                "ingested_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "status": "already_complete",
                "complete": True,
            }

        # Get cursor date (how far back we've synced)
        cursor_date = integration_obj.historical_backfill_cursor_date
        if not cursor_date:
            logger.warning(
                f"[GARMIN_HISTORY] No cursor_date set for user_id={user_id}. "
                "Initial backfill should set this. Skipping history backfill."
            )
            return {
                "ingested_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "status": "no_cursor",
            }

        # Get user's account creation date (stop condition)
        user = session.execute(select(User).where(User.id == user_id)).first()
        if not user:
            logger.error(f"[GARMIN_HISTORY] User not found: user_id={user_id}")
            return {"ingested_count": 0, "skipped_count": 0, "error_count": 0, "status": "user_not_found"}

        user_obj = user[0]
        account_creation_date = user_obj.created_at

        # Define chunk window: [cursor - 90 days, cursor)
        chunk_end = cursor_date
        chunk_start = cursor_date - timedelta(days=90)

        # Stop if we've reached account creation date
        if chunk_start <= account_creation_date:
            logger.info(
                f"[GARMIN_HISTORY] Reached account creation date ({account_creation_date.date()}) "
                f"for user_id={user_id}. Marking as complete."
            )
            integration_obj.historical_backfill_complete = True
            integration_obj.historical_backfill_cursor_date = account_creation_date
            session.commit()
            return {
                "ingested_count": 0,
                "skipped_count": 0,
                "error_count": 0,
                "status": "complete",
                "complete": True,
                "cursor_date": account_creation_date.isoformat(),
            }

        logger.info(
            f"[GARMIN_HISTORY] Processing chunk: {chunk_start.date()} to {chunk_end.date()} "
            f"for user_id={user_id}"
        )

        try:
            # Get Garmin client
            client = get_garmin_client(user_id)
        except ValueError as e:
            logger.error(f"[GARMIN_HISTORY] Failed to get Garmin client: {e}")
            return {"ingested_count": 0, "skipped_count": 0, "error_count": 0, "status": "client_error", "error": str(e)}

        # Backfill stats
        ingested_count = 0
        skipped_count = 0
        error_count = 0
        duplicate_count = 0
        strava_duplicate_count = 0
        total_fetched = 0
        max_pages_per_run = 20  # Safety limit: max 20 pages per chunk (2000 activities)

        try:
            # Fetch activities page by page
            activities_found = False
            for page_num, activities_page in enumerate(
                client.yield_activity_summaries(
                    start_date=chunk_start,
                    end_date=chunk_end,
                    per_page=100,
                    max_pages=max_pages_per_run,
                    sleep_seconds=0.5,  # Rate limit safety
                )
            ):
                if page_num >= max_pages_per_run:
                    logger.warning(
                        f"[GARMIN_HISTORY] Reached max pages limit ({max_pages_per_run}) for chunk, "
                        f"stopping. Will resume on next run."
                    )
                    break

                if activities_page:
                    activities_found = True
                    total_fetched += len(activities_page)

                # Process each activity
                for activity_item in activities_page:
                    activity_payload: dict[str, Any] = activity_item
                    result, ingested, skipped, error = _process_history_activity(
                        session, user_id, activity_payload
                    )
                    ingested_count += ingested
                    skipped_count += skipped
                    error_count += error
                    if result == "skipped_duplicate":
                        duplicate_count += 1
                    elif result == "skipped_strava_duplicate":
                        strava_duplicate_count += 1

                # Commit after each page to avoid memory bloat
                session.commit()

        except Exception as e:
            logger.exception(f"[GARMIN_HISTORY] Chunk backfill failed: {e}")
            error_count += 1

        # Update cursor: move backwards by 90 days (or to account creation date if closer)
        new_cursor = max(chunk_start, account_creation_date)
        integration_obj.historical_backfill_cursor_date = new_cursor

        # Mark complete if we've reached account creation date or no activities found
        if new_cursor <= account_creation_date or (not activities_found and total_fetched == 0):
            integration_obj.historical_backfill_complete = True
            logger.info(
                f"[GARMIN_HISTORY] Historical backfill complete for user_id={user_id} "
                f"(reached {new_cursor.date()})"
            )

        session.commit()

        logger.info(
            f"[GARMIN_HISTORY] Chunk complete for user_id={user_id}: "
            f"ingested={ingested_count}, skipped={skipped_count} "
            f"(duplicates={duplicate_count}, strava_duplicates={strava_duplicate_count}), "
            f"errors={error_count}, total_fetched={total_fetched}, "
            f"cursor={new_cursor.date()}, complete={integration_obj.historical_backfill_complete}"
        )

        return {
            "ingested_count": ingested_count,
            "skipped_count": skipped_count,
            "duplicate_count": duplicate_count,
            "strava_duplicate_count": strava_duplicate_count,
            "error_count": error_count,
            "total_fetched": total_fetched,
            "cursor_date": new_cursor.isoformat(),
            "complete": integration_obj.historical_backfill_complete,
            "status": "completed" if integration_obj.historical_backfill_complete else "partial",
        }
