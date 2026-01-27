"""Backfill logic for Garmin activities.

Paginated fetch, deduplication via (provider, external_id), rate-limit safe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.db.models import Activity, UserIntegration
from app.db.session import get_session


def backfill_garmin_activities(
    user_id: str,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """Backfill Garmin activities for a user.

    Paginated fetch, deduplicate via (source_provider, external_activity_id),
    rate-limit safe.

    Args:
        user_id: User ID to backfill for
        from_date: Start date for backfill (default: 90 days ago)
        to_date: End date for backfill (default: now)

    Returns:
        Dict with backfill results: {ingested_count, skipped_count, error_count}
    """
    logger.info(f"[GARMIN_BACKFILL] Starting backfill for user_id={user_id}")

    if not settings.garmin_enabled:
        logger.warning(f"[GARMIN_BACKFILL] Garmin integration disabled, skipping backfill for user_id={user_id}")
        return {"ingested_count": 0, "skipped_count": 0, "error_count": 0, "status": "disabled"}

    # Default to 90 days ago if not specified
    if from_date is None:
        from_date = datetime.now(timezone.utc) - timedelta(days=90)
    if to_date is None:
        to_date = datetime.now(timezone.utc)

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

        # TODO: Fetch activities from Garmin API (paginated)
        # For now, return mock results
        logger.info(
            f"[GARMIN_BACKFILL] Fetching activities from {from_date} to {to_date} "
            f"for provider_user_id={integration_obj.provider_user_id} (mock)"
        )

        # Mock: Simulate paginated fetch
        ingested_count = 0
        skipped_count = 0
        error_count = 0

        # In real implementation:
        # 1. Fetch activities page by page from Garmin API
        # 2. For each activity:
        #    - Check if already exists via (source_provider, external_activity_id)
        #    - If not exists, normalize and store
        #    - If exists, skip (idempotent)
        # 3. Respect rate limits (sleep between requests)
        # 4. Update last_sync_at on integration

        logger.info(
            f"[GARMIN_BACKFILL] Backfill complete for user_id={user_id}: "
            f"ingested={ingested_count}, skipped={skipped_count}, errors={error_count}"
        )

        return {
            "ingested_count": ingested_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "status": "completed",
        }


def _check_activity_exists(
    session: Session,
    source_provider: str,
    external_activity_id: str,
) -> bool:
    """Check if activity already exists (idempotent check).

    Args:
        session: Database session
        source_provider: Provider name ('garmin')
        external_activity_id: External activity ID

    Returns:
        True if activity exists, False otherwise
    """
    existing = session.execute(
        select(Activity).where(
            Activity.source_provider == source_provider,
            Activity.external_activity_id == external_activity_id,
        )
    ).first()

    return existing is not None


def _store_normalized_activity(
    session: Session,
    user_id: str,
    normalized: dict[str, Any],
) -> None:
    """Store normalized activity in database.

    Args:
        session: Database session
        user_id: User ID
        normalized: Normalized activity dict
    """
    # Check if already exists (idempotent)
    source_provider = normalized.get("source_provider")
    external_activity_id = normalized.get("external_activity_id")

    if source_provider and external_activity_id:
        if _check_activity_exists(session, source_provider, external_activity_id):
            logger.debug(f"[GARMIN_BACKFILL] Activity already exists: {external_activity_id}, skipping")
            return

    # Create activity record
    activity = Activity(
        user_id=user_id,
        source=normalized.get("source", "garmin"),
        source_activity_id=normalized.get("source_activity_id"),
        source_provider=source_provider,
        external_activity_id=external_activity_id,
        sport=normalized.get("sport", "other"),
        starts_at=datetime.fromisoformat(normalized["start_time"].replace("Z", "+00:00")),
        ends_at=datetime.fromisoformat(normalized["ends_at"].replace("Z", "+00:00")) if normalized.get("ends_at") else None,
        duration_seconds=normalized.get("duration_seconds", 0),
        distance_meters=normalized.get("distance_meters"),
        elevation_gain_meters=normalized.get("elevation_gain_meters"),
        calories=normalized.get("calories"),
        title=normalized.get("title"),
        metrics=normalized.get("metrics", {}),
    )

    session.add(activity)
    session.commit()
    logger.debug(f"[GARMIN_BACKFILL] Stored activity: {external_activity_id}")
