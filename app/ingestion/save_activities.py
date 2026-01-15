"""Save activity records to the database.

LEGACY: This module maps from old ActivityRecord format to new Activity model.
The new ingestion system (ingestion_strava.py, background_sync.py) directly uses the new Activity schema.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, StravaAccount, UserSettings
from app.metrics.effort_service import compute_activity_effort
from app.metrics.load_computation import AthleteThresholds, compute_activity_tss
from app.pairing.auto_pairing_service import try_auto_pair
from app.state.models import ActivityRecord


def _get_user_id_from_athlete_id(session: Session, athlete_id: int) -> str | None:
    """Map athlete_id (Strava) to user_id (Clerk).

    Args:
        session: Database session
        athlete_id: Strava athlete ID (int)

    Returns:
        User ID (Clerk string) or None if not found
    """
    account = session.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id))).first()

    if account:
        return account[0].user_id
    return None


def save_activity_record(
    session: Session,
    record: ActivityRecord,
    raw_json: dict | None = None,
    streams_data: dict | None = None,
) -> Activity:
    """Save a single ActivityRecord to the database.

    LEGACY: Maps from old ActivityRecord (athlete_id) to new Activity (user_id).

    Args:
        session: Database session
        record: ActivityRecord to save (must include athlete_id)
        raw_json: Full raw JSON data from Strava API (optional, stored as-is)
        streams_data: Time-series streams data (GPS, HR, power, etc.) (optional)

    Returns:
        Saved Activity model instance

    Raises:
        ValueError: If athlete_id cannot be mapped to user_id
    """
    logger.debug(
        f"[SAVE_ACTIVITIES] save_activity_record called: activity_id={record.activity_id}, "
        f"athlete_id={record.athlete_id}, raw_json_present={raw_json is not None}, "
        f"raw_json_type={type(raw_json) if raw_json else None}"
    )

    # Map athlete_id to user_id
    logger.debug(f"[SAVE_ACTIVITIES] Mapping athlete_id={record.athlete_id} to user_id")
    user_id = _get_user_id_from_athlete_id(session, record.athlete_id)
    if not user_id:
        raise ValueError(f"Cannot map athlete_id={record.athlete_id} to user_id. Strava account not found.")
    logger.debug(f"[SAVE_ACTIVITIES] Mapped athlete_id={record.athlete_id} to user_id={user_id}")

    # Extract strava_activity_id from activity_id (format: "strava-12345")
    strava_id = record.activity_id
    if strava_id.startswith("strava-"):
        strava_id = strava_id[7:]  # Remove "strava-" prefix
    logger.debug(f"[SAVE_ACTIVITIES] Extracted strava_id={strava_id} from activity_id={record.activity_id}")

    # Check if activity already exists
    logger.debug(f"[SAVE_ACTIVITIES] Checking for existing activity: user_id={user_id}, strava_activity_id={strava_id}")
    existing = (
        session.query(Activity)
        .filter_by(
            user_id=user_id,
            strava_activity_id=strava_id,
        )
        .first()
    )
    logger.debug(f"[SAVE_ACTIVITIES] Existing activity check result: {existing is not None}")

    if existing:
        return _update_existing_activity(
            session=session,
            existing=existing,
            record=record,
            raw_json=raw_json,
            streams_data=streams_data,
            strava_id=strava_id,
            user_id=user_id,
        )

    return _create_new_activity(
        session=session,
        record=record,
        raw_json=raw_json,
        streams_data=streams_data,
        strava_id=strava_id,
        user_id=user_id,
    )


def _update_existing_activity(
    *,
    session: Session,
    existing: Activity,
    record: ActivityRecord,
    raw_json: dict | None,
    streams_data: dict | None,
    strava_id: str,
    user_id: str,
) -> Activity:
    """Update existing activity record."""
    logger.info(f"[SAVE_ACTIVITIES] Activity {strava_id} already exists for user {user_id}, updating")
    existing.start_time = record.start_time
    existing.type = record.sport.capitalize()
    existing.duration_seconds = record.duration_sec
    existing.distance_meters = record.distance_m
    existing.elevation_gain_meters = record.elevation_m
    
    data_updated = False
    if raw_json is not None:
        existing.raw_json = raw_json
        data_updated = True
    elif record.avg_hr is not None:
        if existing.raw_json is None:
            existing.raw_json = {}
        existing.raw_json["average_heartrate"] = record.avg_hr
        data_updated = True
    if streams_data is not None:
        existing.streams_data = streams_data
        data_updated = True
    
    # Recompute effort metrics and TSS if data was updated
    # TSS will use the best available data (streams > HR > RPE)
    if data_updated:
        _compute_and_persist_effort(session, existing)
    
    return existing


def _prepare_raw_json(
    raw_json: dict | None,
    record: ActivityRecord,
) -> dict | None:
    """Prepare raw_json for new activity."""
    if raw_json is None:
        logger.debug("[SAVE_ACTIVITIES] raw_json not provided, building minimal dict")
        prepared_json = {}
        if record.avg_hr is not None:
            prepared_json["average_heartrate"] = record.avg_hr
        if record.power is not None:
            prepared_json.update(record.power)
        if not prepared_json:
            logger.debug("[SAVE_ACTIVITIES] No raw_json data, setting to None")
            return None
        logger.debug(f"[SAVE_ACTIVITIES] Built minimal raw_json with keys: {list(prepared_json.keys())}")
        return prepared_json
    raw_json_keys = list(raw_json.keys()) if isinstance(raw_json, dict) else []
    logger.debug(f"[SAVE_ACTIVITIES] Using provided raw_json with {len(raw_json_keys)} keys: {raw_json_keys[:10]}")
    if "id" in raw_json:
        logger.debug(f"[SAVE_ACTIVITIES] raw_json contains 'id' field: {raw_json.get('id')}")
    return raw_json


def _create_new_activity(
    *,
    session: Session,
    record: ActivityRecord,
    raw_json: dict | None,
    streams_data: dict | None,
    strava_id: str,
    user_id: str,
) -> Activity:
    """Create new activity record."""
    logger.info(f"[SAVE_ACTIVITIES] Creating new activity: {strava_id} for user {user_id}")
    logger.debug(
        f"[SAVE_ACTIVITIES] Processing raw_json: provided={raw_json is not None}, "
        f"type={type(raw_json)}, is_dict={isinstance(raw_json, dict) if raw_json else False}"
    )
    prepared_raw_json = _prepare_raw_json(raw_json, record)
    logger.debug(
        f"[SAVE_ACTIVITIES] Creating Activity object: user_id={user_id}, "
        f"strava_activity_id={strava_id}, type={record.sport.capitalize()}, "
        f"start_time={record.start_time}, duration_seconds={record.duration_sec}, "
        f"distance_meters={record.distance_m}, elevation_gain_meters={record.elevation_m}, "
        f"raw_json_type={type(prepared_raw_json)}, raw_json_keys={len(prepared_raw_json) if isinstance(prepared_raw_json, dict) else 0}"
    )
    activity = Activity(
        user_id=user_id,
        athlete_id=str(record.athlete_id),
        strava_activity_id=strava_id,
        source=record.source,
        start_time=record.start_time,
        type=record.sport.capitalize(),
        duration_seconds=record.duration_sec,
        distance_meters=record.distance_m,
        elevation_gain_meters=record.elevation_m,
        raw_json=prepared_raw_json,
        streams_data=streams_data,
    )
    activity_id = getattr(activity, "id", None)
    logger.debug(
        f"[SAVE_ACTIVITIES] Activity object created: id={activity_id}, "
        f"id_type={type(activity_id)}, user_id={activity.user_id}, "
        f"strava_activity_id={activity.strava_activity_id}, "
        f"raw_json_present={activity.raw_json is not None}, "
        f"raw_json_type={type(activity.raw_json) if activity.raw_json else None}"
    )
    if activity.raw_json and isinstance(activity.raw_json, dict):
        raw_json_keys = list(activity.raw_json.keys())
        logger.debug(
            f"[SAVE_ACTIVITIES] raw_json has {len(raw_json_keys)} keys, "
            f"has 'id': {'id' in raw_json_keys}, sample keys: {raw_json_keys[:10]}"
        )
        if "id" in activity.raw_json:
            logger.debug(f"[SAVE_ACTIVITIES] raw_json['id'] = {activity.raw_json.get('id')}, type: {type(activity.raw_json.get('id'))}")
    logger.debug(f"[SAVE_ACTIVITIES] Adding activity to session: {strava_id}")
    session.add(activity)
    logger.debug(f"[SAVE_ACTIVITIES] Activity added to session, session.dirty: {len(session.dirty)}, session.new: {len(session.new)}")
    if activity in session.new:
        logger.debug("[SAVE_ACTIVITIES] Activity confirmed in session.new")
    else:
        logger.warning("[SAVE_ACTIVITIES] Activity NOT in session.new after add!")
    # Compute effort metrics and TSS (TSS can be computed even without streams_data using HR/RPE fallbacks)
    _compute_and_persist_effort(session, activity)

    # Attempt auto-pairing with planned sessions
    try:
        try_auto_pair(activity=activity, session=session)
    except Exception as e:
        logger.warning(f"[SAVE_ACTIVITIES] Auto-pairing failed for activity {strava_id}: {e}")

    logger.info(f"[SAVE_ACTIVITIES] Added new activity: {strava_id} for user {user_id}")
    return activity


def _compute_and_persist_effort(session: Session, activity: Activity) -> None:
    """Compute and persist effort metrics and TSS for an activity.

    TSS can be computed with or without streams_data:
    - With streams_data: Uses pace-based TSS (running) or power-based TSS (cycling) for highest accuracy
    - Without streams_data: Falls back to HR-based TRIMP or RPE-based TSS

    Args:
        session: Database session
        activity: Activity record
    """
    try:
        # Get user settings for threshold configuration
        user_settings = session.query(UserSettings).filter_by(user_id=activity.user_id).first()

        # Compute effort metrics (requires streams_data for pace/power-based effort)
        if activity.streams_data is not None:
            normalized_effort, effort_source, intensity_factor = compute_activity_effort(activity, user_settings)

            # Persist effort metrics to activity
            activity.normalized_power = normalized_effort
            activity.effort_source = effort_source
            activity.intensity_factor = intensity_factor

            if normalized_effort is not None:
                logger.debug(
                    f"[SAVE_ACTIVITIES] Computed effort for activity {activity.id}: "
                    f"normalized_effort={normalized_effort}, source={effort_source}, IF={intensity_factor}"
                )
        else:
            logger.debug(
                f"[SAVE_ACTIVITIES] Skipping effort computation for activity {activity.id}: streams not available yet"
            )

        # Compute and persist TSS (works with or without streams_data)
        # TSS will use pace/power if streams available, otherwise falls back to HR/RPE
        athlete_thresholds = _build_athlete_thresholds(user_settings)
        tss = compute_activity_tss(activity, athlete_thresholds)
        activity.tss = tss
        activity.tss_version = "v2"

        logger.debug(
            f"[SAVE_ACTIVITIES] Computed TSS for activity {activity.id}: "
            f"tss={tss}, version=v2, streams_available={activity.streams_data is not None}"
        )
    except Exception as e:
        logger.warning(f"[SAVE_ACTIVITIES] Failed to compute effort/TSS for activity {activity.id}: {e}")


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


def save_activity_records(session: Session, records: list[ActivityRecord]) -> int:
    """Save multiple ActivityRecords to the database.

    Args:
        session: Database session
        records: List of ActivityRecords to save

    Returns:
        Number of activities saved (including updates)
    """
    if not records:
        logger.info("[SAVE_ACTIVITIES] No activity records to save")
        return 0

    logger.info(f"[SAVE_ACTIVITIES] Saving {len(records)} activity records to database")
    saved_count = 0

    for record in records:
        try:
            save_activity_record(session, record)
            saved_count += 1
        except Exception as e:
            logger.error(f"[SAVE_ACTIVITIES] Error saving activity {record.activity_id}: {e}")
            # Continue with other activities even if one fails
            continue

    session.commit()
    logger.info(f"[SAVE_ACTIVITIES] Successfully saved {saved_count}/{len(records)} activities to database")
    return saved_count
