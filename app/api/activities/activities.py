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
from app.services.ingestion.fetch_streams import fetch_and_save_streams
from app.services.integrations.strava.client import StravaClient
from app.services.integrations.strava.service import get_strava_client

router = APIRouter(prefix="/activities", tags=["activities", "debug"])


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
        success = fetch_and_save_streams(session, client, activity)

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

        return {
            "success": False,
            "message": "Streams data not available for this activity",
            "streams_data": None,
        }
