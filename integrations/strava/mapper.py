from __future__ import annotations

from integrations.strava.models import StravaActivity
from models.activity import ActivityRecord


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
        sport=activity.type.lower(),
        start_time=activity.start_date,
        duration_sec=activity.elapsed_time,
        distance_m=activity.distance,
        elevation_m=activity.total_elevation_gain,
        avg_hr=int(activity.average_heartrate) if activity.average_heartrate else None,
        power={"avg_watts": activity.average_watts} if activity.average_watts is not None else None,
    )
