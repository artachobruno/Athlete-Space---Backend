"""Lazy sample fetching for Garmin activities.

Guardrails:
- Cache samples_fetched_at per activity
- Never fetch samples more than once unless explicitly requested
- Size cap (max N points per stream)
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity
from app.integrations.garmin.client import get_garmin_client


def fetch_and_store_samples(
    session: Session,
    activity_id: str,
    user_id: str,
    max_samples: int = 10000,
) -> bool:
    """Fetch and store activity samples (lazy - only when needed).

    Guardrails:
    - Checks if samples already fetched (cached in metrics.samples_fetched_at)
    - Caps stream size to max_samples
    - Only fetches if not already cached

    Args:
        session: Database session
        activity_id: Activity UUID
        user_id: User ID
        max_samples: Maximum number of sample points per stream

    Returns:
        True if samples were fetched and stored, False if already cached
    """
    logger.info(f"[GARMIN_SAMPLES] Fetching samples for activity_id={activity_id}")

    # Get activity
    activity = session.execute(
        select(Activity).where(
            Activity.id == activity_id,
            Activity.user_id == user_id,
            Activity.source_provider == "garmin",
        )
    ).first()

    if not activity:
        logger.warning(f"[GARMIN_SAMPLES] Activity not found: {activity_id}")
        return False

    activity_obj = activity[0]

    # Check if samples already fetched (cache check)
    if activity_obj.metrics and isinstance(activity_obj.metrics, dict):
        samples_fetched_at = activity_obj.metrics.get("samples_fetched_at")
        if samples_fetched_at:
            logger.debug(f"[GARMIN_SAMPLES] Samples already fetched at {samples_fetched_at}, skipping")
            return False

    # Get external activity ID
    external_activity_id = activity_obj.external_activity_id
    if not external_activity_id:
        logger.warning(f"[GARMIN_SAMPLES] No external_activity_id for activity: {activity_id}")
        return False

    # Fetch samples from Garmin API
    try:
        client = get_garmin_client(user_id)
        activity_detail = client.fetch_activity_detail(str(external_activity_id), max_samples=max_samples)

        # Extract streams data
        streams_data = activity_detail.get("streams") or {}

        # Update activity metrics with streams and cache timestamp
        if not activity_obj.metrics:
            activity_obj.metrics = {}

        if isinstance(activity_obj.metrics, dict):
            activity_obj.metrics["streams_data"] = streams_data
            activity_obj.metrics["samples_fetched_at"] = datetime.now(timezone.utc).isoformat()

        session.commit()
        logger.info(f"[GARMIN_SAMPLES] Successfully fetched and stored samples for activity_id={activity_id}")
    except Exception as e:
        logger.exception(f"[GARMIN_SAMPLES] Failed to fetch samples for activity_id={activity_id}: {e}")
        session.rollback()
        return False
    else:
        return True
