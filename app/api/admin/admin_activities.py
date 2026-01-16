from fastapi import APIRouter

from app.db.models import Activity
from app.db.session import get_session

router = APIRouter(prefix="/admin/activities", tags=["admin"])


@router.get("/recent")
def recent_activities(limit: int = 10):
    with get_session() as session:
        rows = session.query(Activity).order_by(Activity.starts_at.desc()).limit(limit).all()

    return [
        {
            "source": "strava",  # All activities are from Strava
            "activity_id": r.strava_activity_id,
            "start_time": r.start_time.isoformat(),
            "distance_meters": r.distance_meters,
        }
        for r in rows
    ]
