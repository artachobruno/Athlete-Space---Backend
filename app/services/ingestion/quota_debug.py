from app.services.ingestion.quota_manager import quota_manager


def get_quota_state() -> dict:
    r = quota_manager.redis
    return {
        "used_15m": r.get("strava:quota:15m:used"),
        "used_daily": r.get("strava:quota:daily:used"),
    }
