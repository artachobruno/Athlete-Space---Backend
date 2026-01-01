from __future__ import annotations

import datetime as dt

from integrations.strava.client import StravaClient
from integrations.strava.mapper import map_strava_activity
from models.activity import ActivityRecord


def ingest_strava_activities(
    *,
    client: StravaClient,
    athlete_id: int,
    since: dt.datetime,
    until: dt.datetime,
) -> list[ActivityRecord]:
    """Fetch + normalize Strava activities into domain records.

    Args:
        client: Strava API client
        athlete_id: Athlete ID for multi-user support
        since: Start datetime for activity fetch
        until: End datetime for activity fetch

    Returns:
        List of ActivityRecord objects with athlete_id included
    """
    raw_activities = client.fetch_activities(
        since=since,
        until=until,
    )

    return [map_strava_activity(a, athlete_id=athlete_id) for a in raw_activities]
