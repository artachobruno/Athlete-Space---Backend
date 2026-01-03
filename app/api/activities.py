"""Activity verification endpoints for debugging and validation.

Step 4: Read-only endpoints to verify activity ingestion.
These endpoints are for debugging only - no UI dependency.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy import select

from app.core.auth import get_current_user
from app.state.db import get_session
from app.state.models import Activity

router = APIRouter(prefix="/activities", tags=["activities", "debug"])


@router.get("")
def get_activities(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: str = Depends(get_current_user),
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
    user_id: str = Depends(get_current_user),
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

        # Return full activity including raw_json
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
            "created_at": activity.created_at.isoformat(),
        }
