from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks

from app.ingestion.tasks import backfill_task, incremental_task

router = APIRouter(prefix="/admin/retry", tags=["admin"])


@router.post("/strava/{athlete_id}")
def retry_strava_user(athlete_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(incremental_task, athlete_id)
    background_tasks.add_task(backfill_task, athlete_id)
    return {"status": "scheduled", "athlete_id": athlete_id}
