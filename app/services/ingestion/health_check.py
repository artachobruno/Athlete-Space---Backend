"""Health check for ingestion system.

This module provides health monitoring and alerting for ingestion SLAs.
"""

import time

from loguru import logger

from app.db.models import StravaAuth
from app.db.session import get_session
from app.services.ingestion.sla import SYNC_SLA_SECONDS


def ingestion_health_check() -> None:
    """Check ingestion health and log warnings for stale users.

    Logs:
    - Warning for users who have never synced
    - Error for users who exceed SLA threshold
    """
    now = int(time.time())

    with get_session() as session:
        users = session.query(StravaAuth).all()

    for user in users:
        if not user.last_successful_sync_at:
            logger.warning(f"user={user.athlete_id} never synced")
            continue

        age_seconds = now - user.last_successful_sync_at
        if age_seconds > SYNC_SLA_SECONDS:
            age_minutes = age_seconds // 60
            logger.error(f"user={user.athlete_id} stale ({age_minutes} min)")
