from __future__ import annotations

import datetime as dt

from app.integrations.strava.client import StravaClient
from app.integrations.strava.schemas import map_strava_activity
from app.state.models import ActivityRecord


def ingest_strava_activities(
    *,
    client: StravaClient,
    athlete_id: int,
    since: dt.datetime,
) -> list[ActivityRecord]:
    """Fetch + normalize Strava activities into domain records.

    Args:
        client: Strava API client
        athlete_id: Athlete ID for multi-user support
        since: Start datetime for activity fetch

    Returns:
        List of ActivityRecord objects with athlete_id included
    """
    raw_activities = client.get_activities(after_ts=since)

    return [map_strava_activity(a, athlete_id=athlete_id) for a in raw_activities]
