"""Activity verification endpoints for debugging and validation.

Step 4: Read-only endpoints to verify activity ingestion.
These endpoints are for debugging only - no UI dependency.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.db.models import Activity, StravaAccount
from app.db.session import get_session
from app.ingestion.fetch_streams import fetch_and_save_streams
from app.integrations.strava.client import StravaClient
from app.integrations.strava.service import get_strava_client

router = APIRouter(prefix="/activities", tags=["activities", "debug"])


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

    # GPS route points (latlng)
    latlng = streams_data.get("latlng", [])

    # Elevation over time (altitude in meters)
    altitude = streams_data.get("altitude", [])

    # Pace calculation: velocity_smooth is in m/s, convert to min/km
    # pace_min_per_km = 1000 / (velocity_m_per_s * 60) = 1000 / (velocity * 60)
    velocity_smooth = streams_data.get("velocity_smooth", [])
    pace_min_per_km: list[float | None] = []
    for velocity in velocity_smooth:
        if velocity and velocity > 0:
            # Convert m/s to min/km: (1000 meters) / (velocity m/s * 60 seconds/min)
            pace = (1000.0 / (velocity * 60.0)) if velocity > 0 else None
            pace_min_per_km.append(round(pace, 2) if pace else None)
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
        "route_points": latlng,  # GPS coordinates: [[lat, lng], ...]
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
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user_id),
):
    """Get list of activities for current user (read-only, debug-only).

    Args:
        limit: Maximum number of activities to return (1-100, default: 50)
        offset: Number of activities to skip (default: 0)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        List of activities with pagination metadata
    """
    logger.info(f"[ACTIVITIES] GET /activities called for user_id={user_id}, limit={limit}, offset={offset}")

    with get_session() as session:
        # Get total count
        total_result = session.execute(select(Activity).where(Activity.user_id == user_id))
        total = len(list(total_result))

        # Get paginated activities
        activities_result = session.execute(
            select(Activity).where(Activity.user_id == user_id).order_by(Activity.start_time.desc()).limit(limit).offset(offset)
        )

        activities = []
        for row in activities_result:
            activity = row[0]
            activities.append({
                "id": activity.id,
                "user_id": activity.user_id,
                "strava_activity_id": activity.strava_activity_id,
                "start_time": activity.start_time.isoformat(),
                "type": activity.type,
                "duration_seconds": activity.duration_seconds,
                "distance_meters": activity.distance_meters,
                "elevation_gain_meters": activity.elevation_gain_meters,
                "created_at": activity.created_at.isoformat(),
                "has_raw_json": activity.raw_json is not None,
                "has_streams": activity.streams_data is not None,
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
            """Get Strava client for user."""
            account = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
            if not account:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Strava account not connected",
                )
            return get_strava_client(int(account[0].athlete_id))

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
            logger.error(f"[ACTIVITIES] Error fetching streams for activity {activity_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch streams data: {e!s}",
            ) from e

        if success:
            # Refresh activity to get updated streams_data
            session.refresh(activity)
            data_points = len(activity.streams_data.get("time", [])) if activity.streams_data else 0
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

        if not activity.streams_data:
            fetch_endpoint = f"/activities/{activity_id}/fetch-streams"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Streams data not available for activity {activity_id}. Use POST {fetch_endpoint} to fetch it first.",
            )

        formatted_streams = _format_streams_for_frontend(activity.streams_data)

        if not formatted_streams:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Streams data is empty or invalid for activity {activity_id}",
            )

        return formatted_streams
