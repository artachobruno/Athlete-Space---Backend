from fastapi import APIRouter

from app.ingestion.quota_manager import quota_manager

router = APIRouter(prefix="/admin/status", tags=["admin"])


@router.get("/strava")
def strava_status():
    r = quota_manager.redis
    return {
        "quota": {
            "used_15m": r.get("strava:quota:15m:used"),
            "used_daily": r.get("strava:quota:daily:used"),
        }
    }
