"""Overview service for athlete training data.

Provides overview data without circular dependencies.
Moved from app.api.user.me to break import cycle:
me.py -> ingestion/tasks -> scheduler -> context_builder -> me.py
"""

import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from loguru import logger
from sqlalchemy import func, select

from app.db.models import Activity, DailyTrainingLoad, StravaAccount
from app.db.session import get_session
from app.metrics.daily_aggregation import aggregate_daily_training, get_daily_rows
from app.metrics.data_quality import assess_data_quality


def get_strava_account_for_overview(user_id: str) -> tuple[StravaAccount | None, bool, str | None]:
    """Get StravaAccount for overview service.

    Args:
        user_id: User ID

    Returns:
        Tuple of (account or None, connected: bool, last_sync: str | None)
    """
    if not isinstance(user_id, str):
        error_msg = f"Invalid user_id type: {type(user_id)}. Expected str, got {type(user_id).__name__}"
        logger.error(error_msg)
        raise TypeError(error_msg)

    with get_session() as session:
        result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if not result:
            return None, False, None
        account = result[0]
        session.expunge(account)
        last_sync = account.last_sync_at.isoformat() if account.last_sync_at else None
        return account, True, last_sync


def _extract_today_metrics(metrics_result: dict[str, list[tuple[str, float]]]) -> dict[str, float]:
    """Extract today's CTL, ATL, TSB values and 7-day TSB average from metrics.

    Args:
        metrics_result: Dictionary with "ctl", "atl", "tsb" lists of (date, value) tuples

    Returns:
        Dictionary with today_ctl, today_atl, today_tsb, tsb_7d_avg
    """
    today_ctl = 0.0
    today_atl = 0.0
    today_tsb = 0.0
    tsb_7d_avg = 0.0

    if not isinstance(metrics_result, dict):
        logger.warning(f"[API] metrics_result is not a dict: {type(metrics_result)}")
        return {
            "today_ctl": 0.0,
            "today_atl": 0.0,
            "today_tsb": 0.0,
            "tsb_7d_avg": 0.0,
        }

    tsb_list = metrics_result.get("tsb")
    if tsb_list and isinstance(tsb_list, list) and len(tsb_list) > 0:
        last_item = tsb_list[-1]
        if isinstance(last_item, (list, tuple)) and len(last_item) >= 2:
            today_tsb = float(last_item[1]) if isinstance(last_item[1], (int, float)) else 0.0
            today_date = str(last_item[0])

            ctl_list = metrics_result.get("ctl", [])
            if isinstance(ctl_list, list):
                for date_val, ctl_val in ctl_list:
                    if str(date_val) == today_date:
                        today_ctl = float(ctl_val) if isinstance(ctl_val, (int, float)) else 0.0
                        break

            atl_list = metrics_result.get("atl", [])
            if isinstance(atl_list, list):
                for date_val, atl_val in atl_list:
                    if str(date_val) == today_date:
                        today_atl = float(atl_val) if isinstance(atl_val, (int, float)) else 0.0
                        break

            last_7_tsb = []
            for item in tsb_list[-7:]:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    val = item[1]
                    if isinstance(val, (int, float)):
                        last_7_tsb.append(float(val))
            if last_7_tsb:
                tsb_7d_avg = sum(last_7_tsb) / len(last_7_tsb)

    return {
        "today_ctl": today_ctl,
        "today_atl": today_atl,
        "today_tsb": today_tsb,
        "tsb_7d_avg": tsb_7d_avg,
    }


def _build_overview_response(
    connected: bool,
    last_sync: str | None,
    data_quality_status: str,
    metrics_result: dict[str, list[tuple[str, float]]],
    today_metrics: dict[str, float],
) -> dict:
    """Build overview response dictionary.

    Args:
        connected: Whether Strava account is connected
        last_sync: Last sync timestamp or None
        data_quality_status: Data quality status string
        metrics_result: Training load metrics
        today_metrics: Today's metric values

    Returns:
        Overview response dictionary
    """
    ctl_data = metrics_result.get("ctl", [])
    atl_data = metrics_result.get("atl", [])
    tsb_data = metrics_result.get("tsb", [])

    if not isinstance(ctl_data, list):
        logger.warning(f"[API] CTL data is not a list: {type(ctl_data)}, converting to empty list")
        ctl_data = []
    if not isinstance(atl_data, list):
        logger.warning(f"[API] ATL data is not a list: {type(atl_data)}, converting to empty list")
        atl_data = []
    if not isinstance(tsb_data, list):
        logger.warning(f"[API] TSB data is not a list: {type(tsb_data)}, converting to empty list")
        tsb_data = []

    metrics_data = {
        "ctl": ctl_data,
        "atl": atl_data,
        "tsb": tsb_data,
    }
    today_values = {
        "ctl": round(today_metrics["today_ctl"], 1),
        "atl": round(today_metrics["today_atl"], 1),
        "tsb": round(today_metrics["today_tsb"], 1),
        "tsb_7d_avg": round(today_metrics["tsb_7d_avg"], 1),
    }

    return {
        "connected": connected,
        "last_sync": last_sync,
        "data_quality": data_quality_status,
        "metrics": metrics_data,
        "today": today_values,
    }


def _maybe_trigger_aggregation(user_id: str, activity_count: int, daily_rows: list, days: int = 60) -> list:
    """Trigger aggregation if needed and return updated daily_rows.

    Args:
        user_id: User ID
        activity_count: Number of activities in database
        daily_rows: Current daily rows list
        days: Number of days to fetch after aggregation

    Returns:
        Updated daily_rows list
    """
    should_aggregate = False
    reason = ""

    if activity_count > 0 and len(daily_rows) == 0:
        should_aggregate = True
        reason = "no daily rows"
    elif activity_count > 0 and len(daily_rows) < days:
        should_aggregate = True
        reason = f"only {len(daily_rows)} days available, {days} requested"

    if should_aggregate:
        logger.info(
            f"[API] /me/overview: Auto-triggering aggregation for user_id={user_id} "
            f"(activities={activity_count}, daily_rows={len(daily_rows)}, reason={reason})"
        )
        try:
            aggregate_daily_training(user_id)
            with get_session() as session:
                daily_rows = get_daily_rows(session, user_id, days=days)
            logger.info(f"[API] /me/overview: Aggregation completed, now have {len(daily_rows)} daily rows (requested {days} days)")
        except Exception:
            logger.exception(f"[API] /me/overview: Failed to auto-aggregate for user_id={user_id}")
    return daily_rows


def get_overview_data(user_id: str, days: int = 7) -> dict:
    """Get athlete training overview data.

    Args:
        user_id: Current authenticated user ID
        days: Number of days to look back (default: 7)

    Returns:
        Overview response dictionary with connected, last_sync, data_quality, metrics, today

    Raises:
        HTTPException: If no Strava account is connected or on error
    """
    if not isinstance(user_id, str):
        error_msg = f"Invalid user_id type: {type(user_id)}. Expected str, got {type(user_id).__name__}"
        logger.error(error_msg)
        raise TypeError(error_msg)

    if days < 1:
        days = 7
    days = min(days, 365)

    request_time = time.time()
    logger.info(
        f"[API] /me/overview called at {datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]} for user_id={user_id}, days={days}"
    )

    _account, strava_connected, last_sync = get_strava_account_for_overview(user_id)

    with get_session() as session:
        activity_count = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar() or 0

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)
        activities_in_range = (
            session.execute(
                select(func.count(Activity.id)).where(
                    Activity.user_id == user_id,
                    func.date(Activity.starts_at) >= start_date,
                    func.date(Activity.starts_at) <= end_date,
                )
            ).scalar()
            or 0
        )

        logger.info(
            f"[API] /me/overview: user_id={user_id}, total_activities={activity_count}, "
            f"activities_in_range({days} days)={activities_in_range}"
        )

        daily_rows = get_daily_rows(session, user_id, days=days)

    daily_rows = _maybe_trigger_aggregation(user_id, activity_count, daily_rows, days=days)

    days_with_training = sum(1 for row in daily_rows if row.get("load_score", 0.0) > 0.0)
    if days_with_training < 90 and activity_count > 0:
        logger.info(
            f"[API] /me/overview: user_id={user_id} has only {days_with_training} days with training "
            f"(need 90). Triggering history backfill."
        )
        try:
            # Lazy import to avoid circular dependency:
            # overview_service -> ingestion/tasks -> scheduler -> context_builder -> overview_service
            from app.ingestion.tasks import history_backfill_task  # noqa: PLC0415

            def trigger_backfill():
                try:
                    history_backfill_task(user_id)
                except Exception as e:
                    logger.exception(f"[API] History backfill failed for user_id={user_id}: {e}")

            threading.Thread(target=trigger_backfill, daemon=True).start()
        except Exception as e:
            logger.warning(f"[API] Failed to trigger history backfill for user_id={user_id}: {e}")

    logger.info(
        f"[API] /me/overview: user_id={user_id}, daily_rows_count={len(daily_rows)}, "
        f"date_range={daily_rows[0]['date']} to {daily_rows[-1]['date']}"
        if daily_rows
        else "none"
    )
    logger.debug(
        f"[API] /me/overview: Sending {len(daily_rows)} days to frontend "
        f"({days_with_training} days with training, {len(daily_rows) - days_with_training} rest days)"
    )

    data_quality_status = assess_data_quality(daily_rows)
    logger.info(f"[API] /me/overview: data_quality={data_quality_status} (requires >=14 days, got {len(daily_rows)} days)")

    try:
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days)

        with get_session() as session:
            daily_load_rows = session.execute(
                select(DailyTrainingLoad)
                .where(
                    DailyTrainingLoad.user_id == user_id,
                    DailyTrainingLoad.day >= start_date,
                    DailyTrainingLoad.day <= end_date,
                )
                .order_by(DailyTrainingLoad.day)
            ).all()

        ctl_data: list[tuple[str, float]] = []
        atl_data: list[tuple[str, float]] = []
        tsb_data: list[tuple[str, float]] = []

        for row in daily_load_rows:
            daily_load_record = row[0]
            date_str = daily_load_record.day.isoformat()
            ctl_data.append((date_str, daily_load_record.ctl or 0.0))
            atl_data.append((date_str, daily_load_record.atl or 0.0))
            tsb_data.append((date_str, daily_load_record.tsb or 0.0))

        metrics_result = {
            "ctl": ctl_data,
            "atl": atl_data,
            "tsb": tsb_data,
        }

        logger.info(
            f"[API] /me/overview: Read {len(daily_load_rows)} days from DailyTrainingLoad table "
            f"(date range: {start_date.isoformat()} to {end_date.isoformat()})"
        )
    except Exception as e:
        logger.exception(f"[API] /me/overview: Error reading from DailyTrainingLoad: {e}")
        metrics_result = {"ctl": [], "atl": [], "tsb": []}

    today_metrics = _extract_today_metrics(metrics_result)

    elapsed = time.time() - request_time
    logger.info(f"[API] /me/overview response: data_quality={data_quality_status}, elapsed={elapsed:.3f}s")

    return _build_overview_response(strava_connected, last_sync, data_quality_status, metrics_result, today_metrics)
