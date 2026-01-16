"""Handler for activity uploads from chat.

Handles creating activities from parsed upload data and persisting to database.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from loguru import logger
from sqlalchemy import select

from app.db.models import Activity, StravaAccount
from app.db.session import get_session
from app.metrics.computation_service import trigger_recompute_on_new_activities
from app.upload.activity_parser import ParsedActivityUpload, parse_activity_upload


def _get_athlete_id_from_user_id(user_id: str) -> str:
    """Get athlete_id from user_id via StravaAccount.

    Args:
        user_id: User ID (Clerk)

    Returns:
        Athlete ID as string (falls back to user_id if not found)
    """
    with get_session() as session:
        result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if result:
            return str(result[0].athlete_id)
        return user_id  # Fallback to user_id if no Strava account


def _generate_upload_hash(parsed: ParsedActivityUpload, user_id: str) -> str:
    """Generate unique hash for upload deduplication.

    Args:
        parsed: Parsed activity data
        user_id: User ID

    Returns:
        Hash string
    """
    content = f"{user_id}:{parsed.start_time.isoformat()}:{parsed.duration_seconds}:{parsed.distance_meters}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def upload_activity_from_chat(
    user_id: str,
    content: str,
) -> tuple[list[str], int]:
    """Upload activity/activities from chat content (CSV or text).

    Args:
        user_id: User ID (Clerk)
        content: CSV content or free text

    Returns:
        Tuple of (list of activity IDs, count of activities created)

    Raises:
        ValueError: If parsing fails
    """
    logger.info(f"[UPLOAD_CHAT] Processing activity upload for user_id={user_id}")

    # Parse activities
    parsed_activities = parse_activity_upload(content)
    logger.info(f"[UPLOAD_CHAT] Parsed {len(parsed_activities)} activities")

    # Get athlete_id
    athlete_id = _get_athlete_id_from_user_id(user_id)

    created_activity_ids: list[str] = []
    created_count = 0

    with get_session() as session:
        for parsed in parsed_activities:
            upload_hash = _generate_upload_hash(parsed, user_id)

            # Check for duplicates by hash
            existing_by_hash = session.execute(
                select(Activity).where(
                    Activity.user_id == user_id,
                    Activity.source == "strava",
                    Activity.source_activity_id == upload_hash,
                )
            ).first()

            if existing_by_hash:
                logger.info(f"[UPLOAD_CHAT] Duplicate detected by hash: {upload_hash[:16]}...")
                created_activity_ids.append(existing_by_hash[0].id)
                continue

            # Check for duplicates by time window (within 2 minutes)
            time_window_start = parsed.start_time - timedelta(seconds=120)
            time_window_end = parsed.start_time + timedelta(seconds=120)

            existing_by_time = session.execute(
                select(Activity).where(
                    Activity.user_id == user_id,
                    Activity.starts_at >= time_window_start,
                    Activity.starts_at <= time_window_end,
                )
            ).first()

            if existing_by_time:
                logger.info(
                    f"[UPLOAD_CHAT] Duplicate detected by time window: "
                    f"parsed_start={parsed.start_time}, existing_start={existing_by_time[0].start_time}"
                )
                created_activity_ids.append(existing_by_time[0].id)
                continue

            # Prepare raw_json with optional fields
            raw_json: dict | None = None
            if parsed.avg_hr is not None or parsed.notes:
                raw_json = {}
                if parsed.avg_hr is not None:
                    raw_json["average_heartrate"] = parsed.avg_hr
                if parsed.notes:
                    raw_json["notes"] = parsed.notes

            # Create activity
            activity = Activity(
                user_id=user_id,
                athlete_id=athlete_id,
                strava_activity_id=upload_hash,
                source="chat_upload",
                start_time=parsed.start_time,
                type=parsed.sport,
                duration_seconds=parsed.duration_seconds,
                distance_meters=parsed.distance_meters,
                elevation_gain_meters=parsed.elevation_gain_meters,
                raw_json=raw_json,
                streams_data=None,
            )

            session.add(activity)
            session.commit()
            session.refresh(activity)

            created_activity_ids.append(activity.id)
            created_count += 1

            logger.info(
                f"[UPLOAD_CHAT] Activity created: id={activity.id}, "
                f"type={activity.type}, date={activity.start_time.date()}"
            )

    # Trigger metrics recomputation
    if created_count > 0:
        try:
            trigger_recompute_on_new_activities(user_id)
            logger.info(f"[UPLOAD_CHAT] Metrics recomputation triggered for user_id={user_id}")
        except Exception as e:
            logger.exception(f"[UPLOAD_CHAT] Failed to trigger metrics recomputation: {e}")

    logger.info(f"[UPLOAD_CHAT] Upload complete: {created_count} new activities created")
    return created_activity_ids, created_count
