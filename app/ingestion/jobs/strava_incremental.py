from __future__ import annotations

import datetime as dt

from loguru import logger

from app.db.helpers import store_activity, update_last_ingested_at
from app.integrations.strava.service import get_strava_client


def incremental_sync_user(user):
    logger.debug(f"[INCREMENTAL] Starting incremental sync for athlete_id={user.athlete_id}")

    now = dt.datetime.now(tz=dt.UTC)
    after = (
        dt.datetime.fromtimestamp(user.last_ingested_at, tz=dt.UTC) if user.last_ingested_at else dt.datetime.fromtimestamp(0, tz=dt.UTC)
    )

    # Always check for recent activities (last 48 hours) to ensure nothing is missing
    # This is a safety check to catch any activities that might have been missed
    recent_check_date = now - dt.timedelta(hours=48)
    if after > recent_check_date:
        # If our sync window is very recent, extend it to cover last 48 hours
        logger.debug(
            f"[INCREMENTAL] Extending sync window to cover last 48 hours for safety check: "
            f"after={after.isoformat()} -> recent_check_date={recent_check_date.isoformat()}"
        )
        after = recent_check_date

    logger.debug(f"[INCREMENTAL] Fetching activities after {after.isoformat()} for athlete_id={user.athlete_id}")
    client = get_strava_client(user.athlete_id)

    activities = client.fetch_recent_activities(after=after)

    if not activities:
        logger.debug(f"[INCREMENTAL] No new activities found for athlete_id={user.athlete_id} (last_ingested_at={user.last_ingested_at})")
        return

    logger.debug(f"[INCREMENTAL] Fetched {len(activities)} new activities for athlete_id={user.athlete_id}")

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

    logger.debug(f"[INCREMENTAL] Saved {saved_count}/{len(activities)} activities for athlete_id={user.athlete_id}")

    newest_ts = max(int(act.start_date.timestamp()) for act in activities)
    update_last_ingested_at(user.athlete_id, newest_ts)
    logger.debug(f"[INCREMENTAL] Updated last_ingested_at to {newest_ts} for athlete_id={user.athlete_id}")
