"""Garmin activity ingestion — webhook payload only, no fetch.

Input: One activity summary from webhook. Dedupe by activityId, map to Activity, store.
DO NOT fetch details during ingest. Details are fetched lazily (samples.py) when needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Activity, UserIntegration
from app.integrations.garmin.backfill import check_garmin_activity_exists, check_strava_duplicate
from app.integrations.garmin.normalize import normalize_garmin_activity
from app.workouts.workout_factory import WorkoutFactory


def ingest_activity_summary(
    session: Session,
    user_id: str,
    summary: dict[str, Any],
    is_update: bool = False,
) -> Literal["duplicate", "ingested", "updated", "skipped_strava_duplicate", "error"]:
    """Ingest one activity summary from webhook payload. No fetch.

    Deduplicate by activityId, map Garmin → Activity, store summary fields only.

    Args:
        session: Database session
        user_id: User ID (from integration lookup via provider_user_id)
        summary: Raw webhook payload (activity summary). Must contain activityId and
                 enough fields for normalize (e.g. startTimeGMT, duration).
        is_update: True if event is activity.updated

    Returns:
        "duplicate" | "ingested" | "updated" | "skipped_strava_duplicate" | "error"
    """
    try:
        normalized = normalize_garmin_activity(summary)
    except Exception as e:
        logger.warning("[GARMIN_INGEST] Normalization failed: {}", e)
        return "error"

    external_activity_id = normalized.get("external_activity_id")
    if not external_activity_id:
        logger.warning("[GARMIN_INGEST] Summary missing external_activity_id")
        return "error"

    existing_garmin = check_garmin_activity_exists(session, external_activity_id)
    if existing_garmin:
        if is_update:
            _update_activity_metadata(existing_garmin, normalized)
            logger.info("[GARMIN_INGEST] Updated activity: {}", external_activity_id)
            return "updated"
        return "duplicate"

    start_time = datetime.fromisoformat(normalized["start_time"].replace("Z", "+00:00"))
    distance_meters = normalized.get("distance_meters")
    existing_strava = check_strava_duplicate(session, user_id, start_time, distance_meters)
    if existing_strava:
        logger.info(
            "[GARMIN_INGEST] Strava duplicate for Garmin {}: strava_id={}",
            external_activity_id,
            existing_strava.source_activity_id,
        )
        if existing_strava.metrics and isinstance(existing_strava.metrics, dict):
            existing_strava.metrics["garmin_activity_id"] = external_activity_id
            session.commit()
        return "skipped_strava_duplicate"

    try:
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
        session.flush()
        WorkoutFactory.get_or_create_for_activity(session, activity)

        integration = session.execute(
            select(UserIntegration).where(
                UserIntegration.user_id == user_id,
                UserIntegration.provider == "garmin",
            )
        ).first()
        if integration:
            obj = integration[0]
            obj.last_sync_at = datetime.now(timezone.utc)
            obj.garmin_last_webhook_received_at = datetime.now(timezone.utc)

        logger.debug("[GARMIN_INGEST] Stored activity: {}", external_activity_id)
        return "ingested"
    except IntegrityError:
        session.rollback()
        logger.debug("[GARMIN_INGEST] Duplicate during commit (race)")
        return "duplicate"
    except Exception as e:
        logger.exception("[GARMIN_INGEST] Store failed: {}", e)
        session.rollback()
        return "error"


def _update_activity_metadata(activity: Activity, normalized: dict[str, Any]) -> None:
    """Update activity metadata from normalized summary. No overwrite of user edits."""
    if normalized.get("duration_seconds", 0) > 0:
        activity.duration_seconds = normalized["duration_seconds"]
    if normalized.get("distance_meters") is not None:
        activity.distance_meters = normalized["distance_meters"]
    if normalized.get("elevation_gain_meters") is not None:
        activity.elevation_gain_meters = normalized["elevation_gain_meters"]
    if normalized.get("calories") is not None:
        activity.calories = normalized["calories"]
    if normalized.get("metrics"):
        if not activity.metrics:
            activity.metrics = {}
        if isinstance(activity.metrics, dict):
            if "heart_rate" in normalized["metrics"]:
                if "heart_rate" not in activity.metrics:
                    activity.metrics["heart_rate"] = {}
                activity.metrics["heart_rate"].update(normalized["metrics"]["heart_rate"])
            if "raw_json" in normalized["metrics"]:
                activity.metrics["raw_json"] = normalized["metrics"]["raw_json"]
    if normalized.get("ends_at"):
        activity.ends_at = datetime.fromisoformat(normalized["ends_at"].replace("Z", "+00:00"))
