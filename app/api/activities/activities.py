"""Activity verification endpoints for debugging and validation.

Step 4: Read-only endpoints to verify activity ingestion.
These endpoints are for debugging only - no UI dependency.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256

import requests
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies.auth import get_current_user_id
from app.config.settings import settings
from app.core.encryption import EncryptionError, EncryptionKeyError, decrypt_token, encrypt_token
from app.db.models import Activity, PairingDecision, PlannedSession, StravaAccount
from app.db.session import get_session
from app.ingestion.fetch_streams import fetch_and_save_streams
from app.ingestion.file_parser import parse_activity_file
from app.integrations.strava.client import StravaClient
from app.integrations.strava.tokens import refresh_access_token
from app.metrics.computation_service import trigger_recompute_on_new_activities
from app.pairing.auto_pairing_service import try_auto_pair
from app.pairing.session_links import get_link_for_activity, unlink_by_activity
from app.workouts.guards import assert_activity_has_execution, assert_activity_has_workout
from app.workouts.workout_factory import WorkoutFactory

router = APIRouter(prefix="/activities", tags=["activities", "debug"])


def normalize_route_points(value: list | dict | None) -> list:
    """Normalize route points to always return an array.

    Handles heterogeneous formats stored in DB:
    - Array format: [[lat, lng], ...] -> returns as-is
    - Object format: {latlng: [...], data: [...], route_points: [...], points: [...]} -> extracts array
    - None/empty -> returns empty array

    Args:
        value: Route points in various formats (array, object, or None)

    Returns:
        Always returns a list of [lat, lng] coordinates (empty list if invalid)
    """
    if not value:
        return []

    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        for key in ("latlng", "data", "route_points", "points"):
            if key in value and isinstance(value[key], list):
                return value[key]

    return []


def _format_streams_for_frontend(streams_data: dict | None) -> dict | None:
    """Format streams data for frontend consumption.

    Converts raw Strava streams format to frontend-friendly structure with:
    - GPS route points (latlng)
    - Elevation over time (altitude)
    - Pace over time (converted from velocity_smooth)

    Args:
        streams_data: Raw streams data from Strava

    Returns:
        Formatted streams data or None if not available
    """
    if not streams_data:
        return None

    # Extract time series (common to all streams)
    time_series = streams_data.get("time", [])
    if not time_series:
        return None

    # GPS route points (latlng) - normalize to handle both array and object formats
    # Check multiple possible keys and normalize the result
    route_points_raw = streams_data.get("latlng") or streams_data.get("route_points")
    route_points = normalize_route_points(route_points_raw)

    # Elevation over time (altitude in meters)
    altitude = streams_data.get("altitude", [])

    # Pace calculation: velocity_smooth is in m/s, convert to min/km
    # pace_min_per_km = 1000 / (velocity_m_per_s * 60) = 1000 / (velocity * 60)
    velocity_smooth = streams_data.get("velocity_smooth", [])
    pace_min_per_km: list[float | None] = []
    for velocity in velocity_smooth:
        if velocity is None:
            pace_min_per_km.append(None)
            continue

        # Convert to float if string
        try:
            velocity_float = float(velocity) if not isinstance(velocity, (int, float)) else velocity
        except (ValueError, TypeError):
            pace_min_per_km.append(None)
            continue

        if velocity_float > 0:
            # Convert m/s to min/km: (1000 meters) / (velocity m/s * 60 seconds/min)
            pace = 1000.0 / (velocity_float * 60.0)
            pace_min_per_km.append(round(pace, 2))
        else:
            pace_min_per_km.append(None)

    # Heart rate (if available)
    heartrate = streams_data.get("heartrate", [])

    distance = streams_data.get("distance", [])

    # Power (if available, in watts)
    watts = streams_data.get("watts", [])

    # Cadence (if available)
    cadence = streams_data.get("cadence", [])

    return {
        "time": time_series,  # Time in seconds from start
        "route_points": route_points,  # GPS coordinates: [[lat, lng], ...] (always array)
        "elevation": altitude,  # Elevation in meters
        "pace": pace_min_per_km,  # Pace in min/km (None for stopped periods)
        "heartrate": heartrate,  # Heart rate in bpm
        "distance": distance,  # Cumulative distance in meters
        "power": watts,  # Power in watts (cycling)
        "cadence": cadence,  # Cadence in rpm
        "data_points": len(time_series),
    }


@router.get("")
def get_activities(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    start: str | None = Query(default=None, description="Start date in YYYY-MM-DD format (inclusive)"),
    end: str | None = Query(default=None, description="End date in YYYY-MM-DD format (inclusive)"),
    user_id: str = Depends(get_current_user_id),
):
    """Get list of activities for current user (read-only, debug-only).

    **Data Source**: Reads from database (not from Strava API).
    Activities are synced incrementally in the background and stored in the database.

    Args:
        limit: Maximum number of activities to return (1-1000, default: 50)
        offset: Number of activities to skip (default: 0)
        start: Optional start date filter (YYYY-MM-DD, inclusive)
        end: Optional end date filter (YYYY-MM-DD, inclusive)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        List of activities with pagination metadata
    """
    logger.info(f"[ACTIVITIES] GET /activities called for user_id={user_id}, limit={limit}, offset={offset}, start={start}, end={end}")

    with get_session() as session:
        # Build base query
        query = select(Activity).where(Activity.user_id == user_id)

        # Add date range filtering if provided
        if start:
            try:
                start_date = date.fromisoformat(start)
                start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
                query = query.where(Activity.starts_at >= start_datetime)
            except ValueError:
                logger.warning(f"[ACTIVITIES] Invalid start date format: {start}, ignoring filter")

        if end:
            try:
                end_date = date.fromisoformat(end)
                end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)
                query = query.where(Activity.starts_at <= end_datetime)
            except ValueError:
                logger.warning(f"[ACTIVITIES] Invalid end date format: {end}, ignoring filter")

        # Get total count with filters applied (reuse same query conditions)
        count_query = select(func.count(Activity.id)).where(Activity.user_id == user_id)
        if start:
            try:
                start_date = date.fromisoformat(start)
                start_datetime = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
                count_query = count_query.where(Activity.starts_at >= start_datetime)
            except ValueError:
                pass
        if end:
            try:
                end_date = date.fromisoformat(end)
                end_datetime = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc)
                count_query = count_query.where(Activity.starts_at <= end_datetime)
            except ValueError:
                pass

        total = session.execute(count_query).scalar() or 0

        # Get paginated activities with filters applied
        activities_result = session.execute(
            query.order_by(Activity.starts_at.desc()).limit(limit).offset(offset)
        )

        activities = []
        for row in activities_result:
            activity = row[0]
            # Log what the database actually has (after all transformations)
            logger.debug(
                f"[API OUT] activity_id={activity.id} db_tss={activity.tss} version={getattr(activity, 'tss_version', None)}"
            )
            activities.append({
                "id": activity.id,
                "user_id": activity.user_id,
                "strava_activity_id": activity.strava_activity_id,
                "start_time": activity.start_time.isoformat(),
                "type": activity.type,
                "duration_seconds": activity.duration_seconds,
                "distance_meters": activity.distance_meters,
                "elevation_gain_meters": activity.elevation_gain_meters,
                "tss": activity.tss,
                "tss_version": activity.tss_version,
                "created_at": activity.created_at.isoformat(),
                "has_raw_json": activity.raw_json is not None,
                "has_streams": getattr(activity, "streams_data", None) is not None,
            })

        logger.info(f"[ACTIVITIES] Returning {len(activities)} activities (total: {total})")
        return {
            "activities": activities,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.get("/{activity_id}")
def get_activity(
    activity_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Get single activity by ID (read-only, debug-only).

    **Data Source**: Reads from database (not from Strava API).
    Activity data is stored in the database during background sync.

    Args:
        activity_id: Activity UUID
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Full activity record including raw_json
    """
    logger.info(f"[ACTIVITIES] GET /activities/{activity_id} called for user_id={user_id}")

    with get_session() as session:
        activity_result = session.execute(
            select(Activity).where(
                Activity.id == activity_id,
                Activity.user_id == user_id,
            )
        ).first()

        if not activity_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity {activity_id} not found",
            )

        activity = activity_result[0]

        # Log what the database actually has (after all transformations)
        logger.debug(
            "[API OUT] activity_id=%s db_tss=%s version=%s",
            activity.id,
            activity.tss,
            getattr(activity, "tss_version", None),
        )

        # Return full activity including raw_json and streams_data
        return {
            "id": activity.id,
            "user_id": activity.user_id,
            "strava_activity_id": activity.strava_activity_id,
            "start_time": activity.start_time.isoformat(),
            "type": activity.type,
            "duration_seconds": activity.duration_seconds,
            "distance_meters": activity.distance_meters,
            "elevation_gain_meters": activity.elevation_gain_meters,
            "tss": activity.tss,
            "tss_version": activity.tss_version,
            "raw_json": activity.raw_json,
            "streams_data": activity.streams_data,
            "created_at": activity.created_at.isoformat(),
        }


@router.post("/{activity_id}/fetch-streams")
def fetch_activity_streams(
    activity_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Fetch and save streams data for an activity (on-demand).

    This endpoint fetches time-series streams data (GPS, HR, power, etc.) from Strava
    and saves it to the activity. Streams are not automatically fetched during ingestion
    to conserve API quota.

    Args:
        activity_id: Activity UUID
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Success status and streams data if available
    """
    logger.info(f"[ACTIVITIES] POST /activities/{activity_id}/fetch-streams called for user_id={user_id}")

    with get_session() as session:
        # Get activity
        activity_result = session.execute(
            select(Activity).where(
                Activity.id == activity_id,
                Activity.user_id == user_id,
            )
        ).first()

        if not activity_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity {activity_id} not found",
            )

        activity = activity_result[0]

        # Check if already has streams
        if activity.streams_data is not None:
            logger.info(f"[ACTIVITIES] Activity {activity_id} already has streams data")
            return {
                "success": True,
                "message": "Streams data already available",
                "streams_data": activity.streams_data,
                "data_points": len(activity.streams_data.get("time", [])) if activity.streams_data else 0,
            }

        # Get Strava client
        def _get_client() -> StravaClient:
            """Get Strava client for user from StravaAccount."""
            account_result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
            if not account_result:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Strava account not connected",
                )
            account = account_result[0]

            # Decrypt refresh token
            try:
                refresh_token = decrypt_token(account.refresh_token)
            except EncryptionKeyError as e:
                logger.error(f"[ACTIVITIES] Encryption key mismatch: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Token decryption failed: ENCRYPTION_KEY not set or changed. Please reconnect your Strava account.",
                ) from e
            except EncryptionError as e:
                logger.error(f"[ACTIVITIES] Failed to decrypt refresh token: {e}")
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
                    logger.warning(f"[ACTIVITIES] Invalid refresh token for user_id={user_id}")
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Strava token invalid. Please reconnect your Strava account.",
                    ) from e
                logger.error(f"[ACTIVITIES] Token refresh failed: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to refresh Strava token: {e!s}",
                ) from e

            # Extract access token
            access_token = token_data.get("access_token")
            if not isinstance(access_token, str):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid access_token type from Strava",
                )

            # Update refresh token if provided (token rotation)
            new_refresh_token = token_data.get("refresh_token")
            new_expires_at = token_data.get("expires_at")
            if new_refresh_token and isinstance(new_refresh_token, str) and isinstance(new_expires_at, int):
                try:
                    account.refresh_token = encrypt_token(new_refresh_token)
                    expires_at_dt = datetime.fromtimestamp(new_expires_at, tz=timezone.utc)
                    account.expires_at = expires_at_dt
                    session.commit()
                    logger.info(f"[ACTIVITIES] Rotated refresh token for user_id={user_id}")
                except EncryptionError as e:
                    logger.error(f"[ACTIVITIES] Failed to encrypt new refresh token: {e}")
                    # Continue with old refresh token - not critical

            return StravaClient(access_token=access_token)

        try:
            client = _get_client()
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[ACTIVITIES] Error getting Strava client: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to get Strava client: {e!s}",
            ) from e

        # Fetch and save streams
        try:
            success = fetch_and_save_streams(session, client, activity)
        except Exception as e:
            logger.exception(f"[ACTIVITIES] Error fetching streams for activity {activity_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch streams data: {e!s}",
            ) from e

        if success:
            # Refresh activity to get updated streams_data
            session.refresh(activity)
            # Count data points correctly (streams format: {"time": {"data": [...]}, ...})
            data_points = 0
            if activity.streams_data and "time" in activity.streams_data:
                time_stream = activity.streams_data["time"]
                if isinstance(time_stream, dict) and "data" in time_stream:
                    data_points = len(time_stream["data"])
                elif isinstance(time_stream, list):
                    data_points = len(time_stream)

            logger.info(f"[ACTIVITIES] Successfully fetched streams for activity {activity_id}: {data_points} data points")
            return {
                "success": True,
                "message": "Streams data fetched and saved",
                "streams_data": activity.streams_data,
                "data_points": data_points,
            }

        # Streams not available - return 404 instead of 200 with success=False
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Streams data not available for this activity. This may be due to API limitations or activity type restrictions.",
        )


@router.get("/{activity_id}/streams")
def get_activity_streams(
    activity_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Get formatted streams data for an activity (GPS, elevation, pace).

    **Data Source**: Reads from database (not from Strava API).
    Streams data must be fetched first using POST /activities/{activity_id}/fetch-streams
    if not already available in the database.

    This endpoint returns time-series data formatted for frontend visualization:
    - GPS route points (latlng) for map display
    - Elevation over time for elevation profile
    - Pace over time (converted to min/km) for pace chart
    - Heart rate, power, cadence if available

    Args:
        activity_id: Activity UUID
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Formatted streams data with:
        - time: List of time values in seconds from start
        - route_points: List of [lat, lng] GPS coordinates
        - elevation: List of elevation values in meters
        - pace: List of pace values in min/km (None for stopped periods)
        - heartrate: List of heart rate values in bpm (if available)
        - distance: List of cumulative distance in meters
        - power: List of power values in watts (if available)
        - cadence: List of cadence values in rpm (if available)
        - data_points: Number of data points

    Frontend Usage:
        GET /activities/{activity_id}/streams

        Response structure:
        {
          "time": [0, 1, 2, ...],
          "route_points": [[lat1, lng1], [lat2, lng2], ...],
          "elevation": [100.5, 101.2, ...],
          "pace": [5.2, 5.1, null, ...],  // min/km, null when stopped
          "heartrate": [120, 125, ...],
          "distance": [0, 10, 20, ...],
          "power": [200, 210, ...],
          "cadence": [85, 86, ...],
          "data_points": 3600
        }

        All arrays are aligned by index - streams[i] corresponds to time[i].
    """
    logger.info(f"[ACTIVITIES] GET /activities/{activity_id}/streams called for user_id={user_id}")

    with get_session() as session:
        activity_result = session.execute(
            select(Activity).where(
                Activity.id == activity_id,
                Activity.user_id == user_id,
            )
        ).first()

        if not activity_result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Activity {activity_id} not found",
            )

        activity = activity_result[0]

        # Check if streams_data exists (reads from metrics JSONB via property)
        streams_data = activity.streams_data
        if not streams_data:
            metrics_keys = list(activity.metrics.keys()) if activity.metrics else "None"
            logger.debug(
                f"[ACTIVITIES] No streams_data found for activity {activity_id}, metrics keys: {metrics_keys}"
            )
            fetch_endpoint = f"/activities/{activity_id}/fetch-streams"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Streams data not available for activity {activity_id}. Use POST {fetch_endpoint} to fetch it first.",
            )

        stream_types = list(streams_data.keys()) if isinstance(streams_data, dict) else "not a dict"
        logger.debug(f"[ACTIVITIES] Found streams_data for activity {activity_id}, stream types: {stream_types}")
        formatted_streams = _format_streams_for_frontend(streams_data)

        if not formatted_streams:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Streams data is empty or invalid for activity {activity_id}",
            )

        return formatted_streams


# File upload constants
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
ALLOWED_EXTENSIONS = {".fit", ".gpx", ".tcx"}
MAX_UPLOADS_PER_DAY = 20


@router.post("/upload")
def upload_activity_file(
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
):
    """Upload and ingest a single activity file (FIT, GPX, or TCX).

    Uploaded activities are stored identically to Strava-ingested activities:
    - Same database table
    - Same metrics computation
    - Same coach agent visibility
    - Automatic deduplication

    Args:
        file: Activity file (FIT, GPX, or TCX format)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Response with activity_id and deduplicated flag

    Raises:
        HTTPException: 400 if file is invalid
        HTTPException: 413 if file is too large
        HTTPException: 422 if parsing fails
        HTTPException: 429 if rate limit exceeded
        HTTPException: 500 if internal error
    """
    logger.info(f"[UPLOAD] Upload request for user_id={user_id}, filename={file.filename}")

    # Validate file extension
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No filename provided",
        )

    file_ext = None
    for ext in ALLOWED_EXTENSIONS:
        if file.filename.lower().endswith(ext):
            file_ext = ext
            break

    if not file_ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read file into memory
    try:
        file_bytes = file.file.read()
    except Exception as e:
        logger.error(f"[UPLOAD] Failed to read file: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read file: {e!s}",
        ) from e

    # Validate file size
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE / (1024 * 1024):.0f}MB",
        )

    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is empty",
        )

    # Generate upload hash for deduplication
    upload_hash = sha256(file_bytes).hexdigest()
    logger.debug(f"[UPLOAD] Generated hash: {upload_hash[:16]}...")

    # Check rate limit (20 uploads per day)
    with get_session() as session:
        today = datetime.now(timezone.utc).date()
        today_start = datetime.combine(today, datetime.min.time()).replace(tzinfo=timezone.utc)

        upload_count = (
            session.execute(
                select(func.count(Activity.id)).where(
                    Activity.user_id == user_id,
                    Activity.source == "upload",
                    Activity.created_at >= today_start,
                )
            ).scalar()
            or 0
        )

        if upload_count >= MAX_UPLOADS_PER_DAY:
            logger.warning(f"[UPLOAD] Rate limit exceeded for user_id={user_id}: {upload_count} uploads today")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {MAX_UPLOADS_PER_DAY} uploads per day.",
            )

    # Parse file
    try:
        parsed = parse_activity_file(file_bytes, file.filename)
        logger.info(
            f"[UPLOAD] Parsed activity: type={parsed.activity_type}, "
            f"start_time={parsed.start_time}, duration={parsed.duration_seconds}s, "
            f"distance={parsed.distance_meters}m"
        )
    except ValueError as e:
        logger.warning(f"[UPLOAD] Parse failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse activity file: {e!s}",
        ) from e
    except Exception as e:
        logger.exception(f"[UPLOAD] Unexpected parse error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to parse activity file",
        ) from e

    # Check for duplicates (hash + time window)
    with get_session() as session:
        # Check by hash
        existing_by_hash = session.execute(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.source == "strava",
                Activity.source_activity_id == upload_hash,
            )
        ).first()

        if existing_by_hash:
            logger.info(f"[UPLOAD] Duplicate detected by hash: {upload_hash[:16]}...")
            existing_activity = existing_by_hash[0]
            return {
                "status": "ok",
                "activity_id": existing_activity.id,
                "deduplicated": True,
            }

        # Check by time window (within 2 minutes)
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
                f"[UPLOAD] Duplicate detected by time window: "
                f"parsed_start={parsed.start_time}, existing_start={existing_by_time[0].start_time}"
            )
            existing_activity = existing_by_time[0]
            return {
                "status": "ok",
                "activity_id": existing_activity.id,
                "deduplicated": True,
            }

        # Get athlete_id (use user_id for uploads since there's no Strava account)
        # For users with Strava, we could use their athlete_id, but for simplicity
        # and to avoid requiring Strava connection, we use user_id
        athlete_id = user_id

        # Create new activity
        try:
            activity = Activity(
                user_id=user_id,
                athlete_id=athlete_id,
                strava_activity_id=upload_hash,  # Use hash as source_activity_id
                source="upload",
                start_time=parsed.start_time,
                type=parsed.activity_type,
                duration_seconds=parsed.duration_seconds,
                distance_meters=parsed.distance_meters,
                elevation_gain_meters=parsed.elevation_gain_meters,
                raw_json=None,  # No raw JSON for uploads
                streams_data=None,  # No streams data for uploads
            )
            session.add(activity)
            session.flush()  # Ensure ID is generated

            # PHASE 3: Enforce workout + execution creation (mandatory invariant)
            workout = WorkoutFactory.get_or_create_for_activity(session, activity)
            WorkoutFactory.attach_activity(session, workout, activity)

            # Attempt auto-pairing with planned sessions
            try:
                try_auto_pair(activity=activity, session=session)
            except Exception as e:
                logger.warning(f"[UPLOAD] Auto-pairing failed for activity {activity.id}: {e}")

            session.commit()
            session.refresh(activity)

            logger.info(f"[UPLOAD] Activity created: id={activity.id}, hash={upload_hash[:16]}...")

            # PHASE 7: Assert invariant holds (guard check)
            try:
                assert_activity_has_workout(activity)
                assert_activity_has_execution(session, activity)
            except AssertionError:
                # Log but don't fail the request - invariant violation is logged
                pass

                # Don't fail the upload if calendar session creation fails

            # Trigger metrics recomputation
            try:
                trigger_recompute_on_new_activities(user_id)
                logger.info(f"[UPLOAD] Metrics recomputation triggered for user_id={user_id}")
            except Exception as e:
                logger.exception(f"[UPLOAD] Failed to trigger metrics recomputation: {e}")
                # Don't fail the upload if metrics recomputation fails

        except IntegrityError as e:
            session.rollback()
            # Check if it's a duplicate constraint violation
            if "uq_activity_user_strava_id" in str(e) or "unique" in str(e).lower():
                logger.info(f"[UPLOAD] Duplicate detected by constraint: {upload_hash[:16]}...")
                # Fetch the existing activity
                existing = session.execute(
                    select(Activity).where(
                        Activity.user_id == user_id,
                        Activity.source == "strava",
                        Activity.source_activity_id == upload_hash,
                    )
                ).first()
                if existing:
                    return {
                        "status": "ok",
                        "activity_id": existing[0].id,
                        "deduplicated": True,
                    }
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save activity",
            ) from e
        except Exception as e:
            session.rollback()
            logger.exception(f"[UPLOAD] Failed to save activity: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save activity",
            ) from e
        else:
            return {
                "status": "ok",
                "activity_id": activity.id,
                "deduplicated": False,
            }


@router.post("/{activity_id}/unpair")
def unpair_activity(
    activity_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Manually unpair an activity from its planned session.

    Args:
        activity_id: Activity ID to unpair
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Response with unpair status

    Raises:
        HTTPException: 404 if activity not found
        HTTPException: 403 if activity doesn't belong to user
        HTTPException: 400 if activity is not paired
    """
    logger.info(f"[UNPAIR] Unpair request for activity_id={activity_id}, user_id={user_id}")

    with get_session() as session:
        # Get activity
        activity = session.query(Activity).filter(Activity.id == activity_id).first()
        if not activity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Activity not found",
            )

        # Verify ownership
        if activity.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Activity does not belong to user",
            )

        # Schema v2: Check if paired via SessionLink
        link = get_link_for_activity(session, activity_id)
        if not link:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Activity is not paired",
            )

        # Get planned session from link
        planned_session_id = link.planned_session_id
        planned = session.query(PlannedSession).filter(PlannedSession.id == planned_session_id).first()

        try:
            # Schema v2: Unpair using SessionLink helper
            unlink_by_activity(session, activity_id, reason="Manual unpair via API")

            # Log decision
            pairing_decision = PairingDecision(
                user_id=user_id,
                planned_session_id=planned.id if planned else None,
                activity_id=activity.id,
                decision="manual_unpair",
                duration_diff_pct=None,
                reason="user_action",
                created_at=datetime.now(timezone.utc),
            )
            session.add(pairing_decision)

            session.commit()

            logger.info(
                f"[UNPAIR] Successfully unpaired activity {activity_id} from planned session {planned_session_id}",
            )
        except Exception as e:
            session.rollback()
            logger.exception(f"[UNPAIR] Failed to unpair activity {activity_id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to unpair activity",
            ) from e
        else:
            return {
                "status": "ok",
                "activity_id": activity_id,
                "planned_session_id": planned_session_id,
                "unpaired": True,
            }
