from __future__ import annotations

import datetime as dt

from integrations.strava.client import StravaClient
from integrations.strava.mapper import map_strava_activity
from models.activity import ActivityRecord


def ingest_strava_activities(
    *,
    client: StravaClient,
    since: dt.datetime,
    until: dt.datetime,
) -> list[ActivityRecord]:
    """Fetch + normalize Strava activities into domain records."""
    raw_activities = client.fetch_activities(
        since=since,
        until=until,
    )

    return [map_strava_activity(a) for a in raw_activities]
