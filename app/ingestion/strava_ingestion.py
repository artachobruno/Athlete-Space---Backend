from __future__ import annotations

import datetime as dt

from loguru import logger

from app.integrations.strava.client import StravaClient
from app.integrations.strava.schemas import map_strava_activity
from models.activity import ActivityRecord


def ingest_strava_activities(
    *,
    client: StravaClient,
    since: dt.datetime,
    until: dt.datetime,
) -> list[ActivityRecord]:
    """Fetch + normalize Strava activities into domain records."""
    logger.info(f"Starting Strava ingestion: since={since.isoformat()}, until={until.isoformat()}")
    try:
        raw_activities = client.fetch_activities(
            since=since,
            until=until,
        )

        logger.debug(f"Mapping {len(raw_activities)} raw activities to domain records")
        records = [map_strava_activity(a) for a in raw_activities]
        logger.info(f"Ingestion complete: {len(records)} activities normalized")
        return records
    except Exception as e:
        logger.error(f"Error during Strava ingestion: {e}")
        raise
