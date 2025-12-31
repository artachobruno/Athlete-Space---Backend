from __future__ import annotations

import datetime as dt

from loguru import logger

from app.db import store_activity, update_last_ingested_at
from app.integrations.strava.service import get_strava_client


def incremental_sync_user(user):
    logger.info(f"[INCREMENTAL] Starting incremental sync for athlete_id={user.athlete_id}")

    after = (
        dt.datetime.fromtimestamp(user.last_ingested_at, tz=dt.UTC) if user.last_ingested_at else dt.datetime.fromtimestamp(0, tz=dt.UTC)
    )

    logger.info(f"[INCREMENTAL] Fetching activities after {after.isoformat()} for athlete_id={user.athlete_id}")
    client = get_strava_client(user.athlete_id)

    activities = client.fetch_recent_activities(after=after)

    if not activities:
        logger.info(f"[INCREMENTAL] No new activities found for athlete_id={user.athlete_id} (last_ingested_at={user.last_ingested_at})")
        return

    logger.info(f"[INCREMENTAL] Fetched {len(activities)} new activities for athlete_id={user.athlete_id}")

    saved_count = 0
    for act in activities:
        try:
            store_activity(
                user_id=user.athlete_id,
                source="strava",
                activity_id=str(act.id),
                start_time=act.start_date,
                raw=act.raw,
            )
            saved_count += 1
        except Exception as e:
            logger.error(f"[INCREMENTAL] Failed to save activity {act.id} for athlete_id={user.athlete_id}: {e}")

    logger.info(f"[INCREMENTAL] Saved {saved_count}/{len(activities)} activities for athlete_id={user.athlete_id}")

    newest_ts = max(int(act.start_date.timestamp()) for act in activities)
    update_last_ingested_at(user.athlete_id, newest_ts)
    logger.info(f"[INCREMENTAL] Updated last_ingested_at to {newest_ts} for athlete_id={user.athlete_id}")
