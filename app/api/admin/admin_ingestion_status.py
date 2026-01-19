import time

from fastapi import APIRouter

from app.db.session import get_session
from app.ingestion.quota_manager import quota_manager
from app.model_aliases import StravaAuth

NOW = int(time.time())

STALE_THRESHOLD = 24 * 3600
STUCK_THRESHOLD = 3 * 3600


def user_state(u) -> str:
    if u.last_error:
        return "error"

    if not u.backfill_done:
        if u.last_ingested_at and NOW - u.last_ingested_at > STUCK_THRESHOLD:
            return "stuck"
        return "backfilling"

    if not u.last_ingested_at or NOW - u.last_ingested_at > STALE_THRESHOLD:
        return "stale"

    return "ok"


router = APIRouter(prefix="/admin/ingestion", tags=["admin"])


@router.get("/strava")
def strava_ingestion_status():
    with get_session() as session:
        users = session.query(StravaAuth).all()

    r = quota_manager.redis

    return {
        "quota": {
            "used_15m": r.get("strava:quota:15m:used"),
            "used_daily": r.get("strava:quota:daily:used"),
        },
        "users": [
            {
                "athlete_id": u.athlete_id,
                "last_ingested_at": u.last_ingested_at,
                "backfill_page": u.backfill_page,
                "backfill_done": u.backfill_done,
                "last_error": u.last_error,
                "state": user_state(u),
            }
            for u in users
        ],
    }
