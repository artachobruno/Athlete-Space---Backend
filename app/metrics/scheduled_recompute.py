"""Scheduled metrics recomputation service.

Periodically recomputes daily training load metrics (CTL, ATL, TSB) for all users.
This ensures metrics stay up-to-date even if individual recomputations fail.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.db.models import Activity
from app.db.session import get_session
from app.metrics.computation_service import recompute_metrics_for_user


def recompute_metrics_for_all_users() -> dict[str, int]:
    """Recompute training load metrics for all users with activities.

    This function is called by the scheduler to periodically recompute metrics
    for all users. It only recomputes the last 50 days (CTL window + buffer)
    to minimize computation time.

    Returns:
        Dictionary with summary statistics (users_processed, users_failed, total_created)
    """
    logger.info("[SCHEDULED_METRICS] Starting scheduled metrics recomputation for all users")

    with get_session() as session:
        # Get all users with activities
        result = session.execute(select(Activity.user_id).distinct().where(Activity.user_id.isnot(None))).all()
        user_ids = [row[0] for row in result if row[0]]

    if not user_ids:
        logger.info("[SCHEDULED_METRICS] No users found with activities")
        return {"users_processed": 0, "users_failed": 0, "total_created": 0}

    logger.info(f"[SCHEDULED_METRICS] Found {len(user_ids)} users with activities")

    users_processed = 0
    users_failed = 0
    total_created = 0

    # Recompute last 50 days (CTL window + buffer) for all users
    since_date = datetime.now(tz=timezone.utc).date() - timedelta(days=50)

    for idx, user_id in enumerate(user_ids, 1):
        try:
            logger.debug(f"[SCHEDULED_METRICS] [{idx}/{len(user_ids)}] Processing user {user_id}")
            result = recompute_metrics_for_user(user_id, since_date=since_date)
            total_created += result.get("daily_created", 0)
            users_processed += 1
        except Exception as e:
            logger.error(f"[SCHEDULED_METRICS] Failed to recompute metrics for user {user_id}: {e}", exc_info=True)
            users_failed += 1

    logger.info(
        f"[SCHEDULED_METRICS] Scheduled recomputation complete: "
        f"users_processed={users_processed}, users_failed={users_failed}, total_created={total_created}"
    )

    return {
        "users_processed": users_processed,
        "users_failed": users_failed,
        "total_created": total_created,
    }
