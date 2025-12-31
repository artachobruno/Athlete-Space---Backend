from fastapi import APIRouter
from loguru import logger

from app.ingestion.scheduler import ingestion_tick

router = APIRouter(prefix="/admin/ingestion", tags=["admin"])


@router.post("/strava/run")
def run_strava_ingestion():
    logger.info("Manual Strava ingestion triggered")
    ingestion_tick()
    return {"status": "enqueued"}
