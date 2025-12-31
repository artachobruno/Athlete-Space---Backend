from fastapi import APIRouter

from app.models import Activity
from app.state.db import get_session

router = APIRouter(prefix="/admin/activities", tags=["admin"])


@router.get("/recent")
def recent_activities(limit: int = 10):
    with get_session() as session:
        rows = session.query(Activity).filter(Activity.source == "strava").order_by(Activity.start_time.desc()).limit(limit).all()

    return [
        {
            "source": r.source,
            "activity_id": r.activity_id,
            "start_time": r.start_time.isoformat(),
            "distance_m": r.distance_m,
        }
        for r in rows
    ]
