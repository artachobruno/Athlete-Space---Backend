"""User-facing API endpoints for athlete status and overview.

These endpoints provide read-only access to athlete sync status and training
overview. No ingestion logic is performed here - all data comes from the database.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import func, select

from app.api.dependencies.auth import get_current_user_id
from app.ingestion.sla import SYNC_SLA_SECONDS
from app.metrics.daily_aggregation import aggregate_daily_training, get_daily_rows
from app.metrics.data_quality import assess_data_quality
from app.metrics.training_load import compute_training_load
from app.state.db import get_session
from app.state.models import Activity, StravaAccount

router = APIRouter(prefix="/me", tags=["me"])


def get_strava_account(user_id: str) -> StravaAccount:
    """Get StravaAccount for current user.

    Args:
        user_id: Current authenticated user ID

    Returns:
        StravaAccount instance (detached from session)

    Raises:
        HTTPException: If no Strava account is connected
    """
    # Validate user_id is actually a string, not a Depends object
    if not isinstance(user_id, str):
        error_msg = f"Invalid user_id type: {type(user_id)}. Expected str, got {type(user_id).__name__}"
        logger.error(error_msg)
        raise TypeError(error_msg)

    with get_session() as session:
        result = session.execute(select(StravaAccount).where(StravaAccount.user_id == user_id)).first()
        if not result:
            logger.warning(f"No Strava account connected for user_id={user_id}")
            raise HTTPException(status_code=404, detail="No Strava account connected. Please complete OAuth at /auth/strava")
        account = result[0]
        # Detach object from session so it can be used after session closes
        session.expunge(account)
        return account


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

    Note:
        When data_quality_status != "ok", metrics are still returned with calculated
        values. The UI should display a "Limited data" badge to indicate the data
        quality status. This matches TrainingPeaks / WKO behavior.
    """
    metrics_data = {
        "ctl": metrics_result["ctl"],
        "atl": metrics_result["atl"],
        "tsb": metrics_result["tsb"],
    }
    today_values = {
        "ctl": round(today_metrics["today_ctl"], 1),
        "atl": round(today_metrics["today_atl"], 1),
        "tsb": round(today_metrics["today_tsb"], 1),
        "tsb_7d_avg": round(today_metrics["tsb_7d_avg"], 1),
    }

    return {
        "connected": True,
        "last_sync": last_sync,
        "data_quality": data_quality_status,
        "metrics": metrics_data,
        "today": today_values,
    }


def _maybe_trigger_aggregation(user_id: str, activity_count: int, daily_rows: list) -> list:
    """Trigger aggregation if needed and return updated daily_rows.

    Args:
        user_id: Clerk user ID (string)
        activity_count: Number of activities in database
        daily_rows: Current daily rows list

    Returns:
        Updated daily_rows list (may be re-fetched after aggregation)
    """
    if activity_count > 0 and len(daily_rows) == 0:
        logger.info(f"[API] /me/overview: Auto-triggering aggregation for user_id={user_id} (activities={activity_count}, daily_rows=0)")
        try:
            aggregate_daily_training(user_id)
            # Re-fetch daily rows after aggregation in a new session
            with get_session() as session:
                daily_rows = get_daily_rows(session, user_id, days=60)
            logger.info(f"[API] /me/overview: Aggregation completed, now have {len(daily_rows)} daily rows")
        except Exception as e:
            logger.error(
                f"[API] /me/overview: Failed to auto-aggregate for user_id={user_id}: {e}",
                exc_info=True,
            )
    return daily_rows


def _determine_sync_state(account: StravaAccount) -> str:
    """Determine sync state based on StravaAccount sync status.

    States:
    - "ok": Last sync was successful and within SLA
    - "syncing": Backfill is in progress (full_history_synced == False)
    - "stale": Last sync is beyond SLA threshold or never happened

    Args:
        account: StravaAccount instance

    Returns:
        Sync state string: "ok" | "syncing" | "stale"
    """
    now = int(time.time())

    # Check if backfill is in progress
    if not account.full_history_synced:
        logger.info(f"Sync state for user_id={account.user_id}: syncing (full_history_synced=False)")
        return "syncing"

    # Check if last sync exists and is within SLA
    if account.last_sync_at:
        age_seconds = now - account.last_sync_at
        age_minutes = age_seconds // 60
        if age_seconds <= SYNC_SLA_SECONDS:
            logger.info(f"Sync state for user_id={account.user_id}: ok (last_sync {age_minutes} minutes ago, within SLA)")
            return "ok"
        logger.info(
            f"Sync state for user_id={account.user_id}: stale "
            f"(last_sync {age_minutes} minutes ago, beyond SLA of {SYNC_SLA_SECONDS // 60} minutes)"
        )
        return "stale"

    # No sync ever happened
    logger.info(f"Sync state for user_id={account.user_id}: stale (no sync ever happened)")
    return "stale"


@router.get("/status")
def get_status(user_id: str = Depends(get_current_user_id)):
    """Get athlete sync status.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        {
            "connected": bool,
            "last_sync": str | null,  # ISO 8601 timestamp or null
            "state": "ok" | "syncing" | "stale"
        }
    """
    try:
        request_time = time.time()
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        logger.info(f"[API] /me/status endpoint called at {now_str} for user_id={user_id}")

        # Get StravaAccount for user
        account = get_strava_account(user_id)

        state = _determine_sync_state(account)

        last_sync = None
        if account.last_sync_at:
            last_sync = datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat()

        # Get activity count to track data retrieval
        with get_session() as session:
            result = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar()
            activity_count = result if result is not None else 0

        elapsed = time.time() - request_time
        logger.info(
            f"Status response: user_id={user_id}, state={state}, "
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


def get_overview_data(user_id: str) -> dict:
    """Get athlete training overview data (internal function).

    Args:
        user_id: Current authenticated user ID

    Returns:
        Overview response dictionary with connected, last_sync, data_quality, metrics, today

    Raises:
        HTTPException: If no Strava account is connected or on error
    """
    # Validate user_id is actually a string, not a Depends object
    if not isinstance(user_id, str):
        error_msg = f"Invalid user_id type: {type(user_id)}. Expected str, got {type(user_id).__name__}"
        logger.error(error_msg)
        raise TypeError(error_msg)

    request_time = time.time()
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    logger.info(f"[API] /me/overview called at {now_str} for user_id={user_id}")

    # Get StravaAccount for user
    account = get_strava_account(user_id)

    last_sync = None
    if account.last_sync_at:
        last_sync = datetime.fromtimestamp(account.last_sync_at, tz=timezone.utc).isoformat()

    # Check if we have activities but no daily rows - trigger aggregation if needed
    with get_session() as session:
        # Count activities for this user
        result_count = session.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar()
        activity_count = result_count if result_count is not None else 0
        daily_rows = get_daily_rows(session, user_id, days=60)

    # Auto-trigger aggregation if needed
    daily_rows = _maybe_trigger_aggregation(user_id, activity_count, daily_rows)

    # Log daily rows info for debugging
    date_range_str = f"{daily_rows[0]['date']} to {daily_rows[-1]['date']}" if daily_rows else "none"
    logger.info(f"[API] /me/overview: user_id={user_id}, daily_rows_count={len(daily_rows)}, date_range={date_range_str}")

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


@router.get("/overview/debug")
def get_overview_debug(user_id: str = Depends(get_current_user_id)):
    """Debug endpoint to visualize overview data directly in browser.

    Returns overview data with server timestamp for debugging frontend mismatches,
    confirming CTL source, and comparing metrics vs today values.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        {
            "server_time": str,  # ISO 8601 timestamp
            "overview": {
                "connected": bool,
                "last_sync": str | null,
                "data_quality": "ok" | "limited" | "insufficient",
                "metrics": {...},
                "today": {...}
            }
        }

    Access at: https://<your-render-url>/me/overview/debug
    """
    try:
        overview = get_overview_data(user_id)
        return {
            "server_time": datetime.now(timezone.utc).isoformat(),
            "overview": overview,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting overview debug: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get overview debug: {e!s}") from e


@router.get("/overview")
def get_overview(user_id: str = Depends(get_current_user_id)):
    """Get athlete training overview.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

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
        - Metrics are always returned with calculated values
        - UI should display "Limited data" badge when data_quality != "ok"
        - Uses derived data (daily_training_summary), not raw activities
    """
    try:
        return get_overview_data(user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting overview: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get overview: {e!s}") from e
