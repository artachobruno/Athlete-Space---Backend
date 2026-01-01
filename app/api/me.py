"""User-facing API endpoints for athlete status and overview.

These endpoints provide read-only access to athlete sync status and training
overview. No ingestion logic is performed here - all data comes from the database.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import NoReturn

from fastapi import APIRouter, HTTPException
from loguru import logger
from sqlalchemy import func, select

from app.ingestion.sla import SYNC_SLA_SECONDS
from app.metrics.daily_aggregation import aggregate_daily_training, get_daily_rows
from app.metrics.data_quality import assess_data_quality
from app.metrics.training_load import compute_training_load
from app.state.db import get_session
from app.state.models import Activity, StravaAuth

router = APIRouter(prefix="/me", tags=["me"])


def _raise_missing_athlete_id_error() -> NoReturn:
    """Raise HTTPException for missing athlete_id."""
    raise HTTPException(status_code=500, detail="athlete_id is missing")


def get_current_user_data() -> dict[str, int | str | None]:
    """Get current user data from StravaAuth record.

    Returns:
        Dictionary with user data: athlete_id, last_error, backfill_done, last_successful_sync_at

    Raises:
        HTTPException: If no user is connected
    """
    with get_session() as session:
        result = session.execute(select(StravaAuth)).first()
        if not result:
            raise HTTPException(status_code=404, detail="No Strava account connected")
        user = result[0]
        # Extract all needed attributes while session is open
        return {
            "athlete_id": user.athlete_id,
            "last_error": user.last_error,
            "backfill_done": getattr(user, "backfill_done", None),
            "last_successful_sync_at": user.last_successful_sync_at,
        }


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

    if metrics_result.get("tsb"):
        today_tsb_list = metrics_result["tsb"]
        if today_tsb_list:
            today_tsb = today_tsb_list[-1][1]
            today_date = today_tsb_list[-1][0]

            # Find corresponding CTL and ATL
            for date_val, ctl_val in metrics_result.get("ctl", []):
                if date_val == today_date:
                    today_ctl = ctl_val
                    break
            for date_val, atl_val in metrics_result.get("atl", []):
                if date_val == today_date:
                    today_atl = atl_val
                    break

            # Calculate 7-day average of TSB
            last_7_tsb = [val for _, val in today_tsb_list[-7:]]
            if last_7_tsb:
                tsb_7d_avg = sum(last_7_tsb) / len(last_7_tsb)

    return {
        "today_ctl": today_ctl,
        "today_atl": today_atl,
        "today_tsb": today_tsb,
        "tsb_7d_avg": tsb_7d_avg,
    }


def _build_overview_response(
    last_sync: str | None,
    data_quality_status: str,
    metrics_result: dict[str, list[tuple[str, float]]],
    today_metrics: dict[str, float],
) -> dict:
    """Build overview response dictionary.

    Args:
        last_sync: Last sync timestamp or None
        data_quality_status: Data quality status string
        metrics_result: Training load metrics
        today_metrics: Today's metric values

    Returns:
        Overview response dictionary
    """
    if data_quality_status != "ok":
        metrics_data = {"ctl": [], "atl": [], "tsb": []}
    else:
        metrics_data = {
            "ctl": metrics_result["ctl"],
            "atl": metrics_result["atl"],
            "tsb": metrics_result["tsb"],
        }

    return {
        "connected": True,
        "last_sync": last_sync,
        "data_quality": data_quality_status,
        "metrics": metrics_data,
        "today": {
            "ctl": round(today_metrics["today_ctl"], 1),
            "atl": round(today_metrics["today_atl"], 1),
            "tsb": round(today_metrics["today_tsb"], 1),
            "tsb_7d_avg": round(today_metrics["tsb_7d_avg"], 1),
        },
    }


def _maybe_trigger_aggregation(athlete_id: int, activity_count: int, daily_rows: list) -> list:
    """Trigger aggregation if needed and return updated daily_rows.

    Args:
        athlete_id: Athlete ID
        activity_count: Number of activities in database
        daily_rows: Current daily rows list

    Returns:
        Updated daily_rows list (may be re-fetched after aggregation)
    """
    if activity_count > 0 and len(daily_rows) == 0:
        logger.info(
            f"[API] /me/overview: Auto-triggering aggregation for athlete_id={athlete_id} (activities={activity_count}, daily_rows=0)"
        )
        try:
            aggregate_daily_training(athlete_id)
            # Re-fetch daily rows after aggregation in a new session
            with get_session() as session:
                daily_rows = get_daily_rows(session, athlete_id, days=60)
            logger.info(f"[API] /me/overview: Aggregation completed, now have {len(daily_rows)} daily rows")
        except Exception as e:
            logger.error(
                f"[API] /me/overview: Failed to auto-aggregate for athlete_id={athlete_id}: {e}",
                exc_info=True,
            )
    return daily_rows


def _determine_sync_state(user_data: dict[str, int | str | None]) -> str:
    """Determine sync state based on user's sync status.

    States:
    - "ok": Last sync was successful and within SLA
    - "syncing": Backfill is in progress (backfill_done == False)
    - "stale": Last sync is beyond SLA threshold
    - "error": Last sync had an error

    Args:
        user_data: Dictionary with user data

    Returns:
        Sync state string: "ok" | "syncing" | "stale" | "error"
    """
    now = int(time.time())
    athlete_id = user_data.get("athlete_id", "unknown")

    # Check for errors first
    last_error = user_data.get("last_error")
    if last_error:
        logger.info(f"Sync state for athlete_id={athlete_id}: error (last_error={last_error})")
        return "error"

    # Check if backfill is in progress
    backfill_done = user_data.get("backfill_done", False)
    if not backfill_done:
        logger.info(f"Sync state for athlete_id={athlete_id}: syncing (backfill_done=False)")
        return "syncing"

    # Check if last sync exists and is within SLA
    last_sync_at = user_data.get("last_successful_sync_at")
    if last_sync_at:
        age_seconds = now - int(last_sync_at)
        age_minutes = age_seconds // 60
        if age_seconds <= SYNC_SLA_SECONDS:
            logger.info(f"Sync state for athlete_id={athlete_id}: ok (last_sync {age_minutes} minutes ago, within SLA)")
            return "ok"
        logger.info(
            f"Sync state for athlete_id={athlete_id}: stale "
            f"(last_sync {age_minutes} minutes ago, beyond SLA of {SYNC_SLA_SECONDS // 60} minutes)"
        )
        return "stale"

    # No sync ever happened
    logger.info(f"Sync state for athlete_id={athlete_id}: stale (no sync ever happened)")
    return "stale"


@router.get("/status")
def get_status():
    """Get athlete sync status.

    Returns:
        {
            "connected": bool,
            "last_sync": str | null,  # ISO 8601 timestamp or null
            "state": "ok" | "syncing" | "stale" | "error"
        }
    """
    try:
        request_time = time.time()
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        logger.info(f"[API] /me/status endpoint called at {now_str}")
        logger.debug("Status check requested")
        user_data = get_current_user_data()
        athlete_id = user_data.get("athlete_id")
        logger.debug(
            f"User data retrieved: athlete_id={athlete_id}, "
            f"backfill_done={user_data.get('backfill_done')}, "
            f"last_sync_at={user_data.get('last_successful_sync_at')}, "
            f"last_error={user_data.get('last_error')}"
        )

        state = _determine_sync_state(user_data)

        last_sync = None
        last_sync_at = user_data.get("last_successful_sync_at")
        if last_sync_at:
            last_sync = datetime.fromtimestamp(int(last_sync_at), tz=timezone.utc).isoformat()

        # Get activity count to track data retrieval
        # Use func.count with activity_id to avoid relying on id column
        with get_session() as session:
            result = session.execute(select(func.count(Activity.activity_id))).scalar()
            activity_count = result if result is not None else 0

        elapsed = time.time() - request_time
        logger.info(
            f"Status response: athlete_id={athlete_id}, state={state}, "
            f"last_sync={last_sync}, activity_count={activity_count}, elapsed={elapsed:.3f}s"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get status: {e!s}") from e
    else:
        return {
            "connected": True,
            "last_sync": last_sync,
            "state": state,
        }


@router.get("/overview")
def get_overview():
    """Get athlete training overview.

    Returns:
        {
            "connected": bool,
            "last_sync": str | null,  # ISO 8601 timestamp or null
            "data_quality": "ok" | "limited" | "insufficient",
            "metrics": {
                "ctl": [(date, value), ...],
                "atl": [(date, value), ...],
                "tsb": [(date, value), ...]
            },
            "today": {
                "ctl": float,
                "atl": float,
                "tsb": float,
                "tsb_7d_avg": float
            }
        }

    Rules:
        - No LLM
        - No inference
        - If data_quality != "ok" → metrics may be empty
        - Uses derived data (daily_training_summary), not raw activities
    """
    try:
        request_time = time.time()
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        logger.info(f"[API] /me/overview endpoint called at {now_str}")
        user_data = get_current_user_data()

        last_sync = None
        last_sync_at = user_data.get("last_successful_sync_at")
        if last_sync_at:
            last_sync = datetime.fromtimestamp(int(last_sync_at), tz=timezone.utc).isoformat()

        # Get daily rows from derived table
        athlete_id_raw = user_data["athlete_id"]
        if athlete_id_raw is None:
            _raise_missing_athlete_id_error()
        athlete_id = int(athlete_id_raw)

        # Check if we have activities but no daily rows - trigger aggregation if needed
        with get_session() as session:
            # Count activities for this athlete
            result_count = session.execute(select(func.count(Activity.activity_id)).where(Activity.athlete_id == athlete_id)).scalar()
            activity_count = result_count if result_count is not None else 0
            daily_rows = get_daily_rows(session, athlete_id, days=60)

        # Auto-trigger aggregation if needed
        daily_rows = _maybe_trigger_aggregation(athlete_id, activity_count, daily_rows)

        # Log daily rows info for debugging
        date_range_str = f"{daily_rows[0]['date']} to {daily_rows[-1]['date']}" if daily_rows else "none"
        logger.info(f"[API] /me/overview: athlete_id={athlete_id}, daily_rows_count={len(daily_rows)}, date_range={date_range_str}")

        # Assess data quality
        data_quality_status = assess_data_quality(daily_rows)
        logger.info(f"[API] /me/overview: data_quality={data_quality_status} (requires >=14 days, got {len(daily_rows)} days)")

        # Compute training load metrics
        metrics_result = compute_training_load(daily_rows)

        # Extract today's values and 7-day TSB average
        today_metrics = _extract_today_metrics(metrics_result)

        elapsed = time.time() - request_time
        logger.info(f"[API] /me/overview response: data_quality={data_quality_status}, elapsed={elapsed:.3f}s")

        return _build_overview_response(last_sync, data_quality_status, metrics_result, today_metrics)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting overview: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get overview: {e!s}") from e
