from __future__ import annotations

from fastapi import APIRouter

from app.ingestion.tasks import backfill_task, incremental_task

router = APIRouter(prefix="/admin/retry", tags=["admin"])


@router.post("/strava/{athlete_id}")
def retry_strava_user(athlete_id: int):
    incremental_task.delay(athlete_id)
    backfill_task.delay(athlete_id)
    return {"status": "enqueued", "athlete_id": athlete_id}
