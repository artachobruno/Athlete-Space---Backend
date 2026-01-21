from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.state.models import ActivityRecord
from app.utils.sport_utils import normalize_sport_type


class StravaActivity(BaseModel):
    id: int
    name: str | None = None  # Strava's original activity title (may be generic)
    type: str
    start_date: datetime
    elapsed_time: int
    distance: float
    total_elevation_gain: float
    average_heartrate: float | None = None
    average_watts: float | None = None

    raw: dict | None = None  # Store raw API response (may contain nested dicts and lists)


def map_strava_activity(activity: StravaActivity, athlete_id: int) -> ActivityRecord:
    """Map Strava activity to ActivityRecord.

    Args:
        activity: Strava activity from API
        athlete_id: Athlete ID for multi-user support

    Returns:
        ActivityRecord with athlete_id included
    """
    return ActivityRecord(
        athlete_id=athlete_id,
        activity_id=f"strava-{activity.id}",
        source="strava",
        sport=normalize_sport_type(activity.type),
        start_time=activity.start_date,
        duration_sec=activity.elapsed_time,
        distance_m=activity.distance,
        elevation_m=activity.total_elevation_gain,
        avg_hr=int(activity.average_heartrate) if activity.average_heartrate else None,
        power={"avg_watts": activity.average_watts} if activity.average_watts is not None else None,
    )
