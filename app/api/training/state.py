import time
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import Row, func, select, text
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user_id
from app.api.user.me import get_overview_data
from app.coach.services.coach_service import get_coach_advice
from app.config.settings import settings
from app.db.models import Activity, DailyTrainingLoad
from app.db.session import SessionLocal, get_session
from app.metrics.load_computation import compute_activity_tss
from app.metrics.training_load import calculate_ctl_atl_tsb

router = APIRouter(prefix="/state", tags=["state"])


def _normalize_tss(tss: float) -> float:
    """Normalize TSS to -100 to 100 scale.

    TSS typically ranges from 0-200+ per day.
    Formula: (TSS / 200) * 200 - 100 = TSS - 100, clamped to -100 to 100.

    Args:
        tss: Training Stress Score (typically 0-200+)

    Returns:
        Normalized TSS in -100 to 100 range
    """
    normalized = tss - 100.0
    return round(max(-100.0, min(100.0, normalized)), 1)


def _normalize_metric(value: float, max_value: float = 100.0) -> float:
    """Normalize a metric (CTL/ATL) to -100 to 100 scale.

    Args:
        value: Metric value (typically 0-max_value)
        max_value: Maximum expected value for normalization (default 100)

    Returns:
        Normalized value in -100 to 100 range
    """
    normalized = (value / max_value) * 200.0 - 100.0
    return round(max(-100.0, min(100.0, normalized)), 1)


def _process_activities_for_tss(activities_query: Sequence[Row]) -> dict[str, dict[str, float]]:
    """Process activities and calculate daily TSS and hours.

    Args:
        activities_query: Sequence of activity rows from database query

    Returns:
        Dictionary mapping date -> {"hours": float, "tss": float}
    """
    daily_data: dict[str, dict[str, float]] = {}
    for row in activities_query:
        # Skip activities with missing required fields
        if row.duration_seconds is None or row.start_time is None:
            logger.warning(f"Skipping activity {row.id}: missing duration_seconds or start_time")
            continue

        activity_date = row.start_time.date().isoformat()
        if activity_date not in daily_data:
            daily_data[activity_date] = {"hours": 0.0, "tss": 0.0}

        daily_data[activity_date]["hours"] += row.duration_seconds / 3600.0

        # Use stored TSS if available, otherwise compute it
        if hasattr(row, "tss") and row.tss is not None:
            activity_tss = float(row.tss)
        else:
            # Fallback: Calculate TSS for this activity (backward compatibility)
            activity_obj = Activity(
                id=row.id,
                user_id="",
                athlete_id="",
                strava_activity_id="",
                start_time=row.start_time,
                type=row.type or "Unknown",
                duration_seconds=row.duration_seconds,
                distance_meters=row.distance_meters or 0.0,
                elevation_gain_meters=row.elevation_gain_meters or 0.0,
                raw_json=row.raw_json,
            )
            activity_tss = compute_activity_tss(activity_obj)
        daily_data[activity_date]["tss"] += activity_tss

    return daily_data


def _normalize_tsb_range(tsb_values: list[float]) -> list[float]:
    """Normalize TSB values to -100 to 100 scale based on their range.

    TSB can be negative or positive, so we normalize based on the actual range
    in the dataset.

    Args:
        tsb_values: List of TSB values

    Returns:
        List of normalized TSB values in -100 to 100 range
    """
    if not tsb_values:
        return []

    # Find the range in the data
    min_tsb = min(tsb_values)
    max_tsb = max(tsb_values)
    range_tsb = max_tsb - min_tsb

    if range_tsb == 0:
        # All values are the same, center at 0
        return [0.0] * len(tsb_values)

    # Normalize to -100 to 100
    normalized = []
    for tsb in tsb_values:
        # Map from [min, max] to [-100, 100]
        normalized_value = ((tsb - min_tsb) / range_tsb) * 200.0 - 100.0
        normalized.append(round(max(-100.0, min(100.0, normalized_value)), 1))

    return normalized


def _get_debug_result(db: Session, days: int) -> dict:
    """Get debug result for training load endpoint.

    Args:
        db: Database session
        days: Number of days to look back

    Returns:
        Debug result dictionary
    """
    total_count = db.execute(text("SELECT COUNT(*) as cnt FROM activities")).fetchone()
    sample_rows = db.execute(text("SELECT id, start_time, duration_seconds FROM activities LIMIT 5")).fetchall()
    return {
        "debug": {
            "total_activities": total_count[0] if total_count else 0,
            "sample_rows": [dict(r._mapping) for r in sample_rows] if sample_rows else [],
            "query_filter": f"start_time >= {datetime.now(timezone.utc) - timedelta(days=days)}",
        }
    }


def _normalize_all_metrics(
    daily_tss: list[float],
    metrics: dict[str, list[float]],
) -> dict[str, list[float]]:
    """Normalize all training load metrics to -100 to 100 scale.

    Args:
        daily_tss: List of daily TSS values
        metrics: Dictionary with "ctl", "atl", "tsb" lists

    Returns:
        Dictionary with normalized "tss", "ctl", "atl", "tsb" lists
    """
    return {
        "tss": [_normalize_tss(tss) for tss in daily_tss],
        "ctl": [_normalize_metric(ctl, max_value=100.0) for ctl in metrics["ctl"]],
        "atl": [_normalize_metric(atl, max_value=100.0) for atl in metrics["atl"]],
        "tsb": _normalize_tsb_range(metrics["tsb"]),
    }


def _build_training_load_response(
    dates: list[str],
    daily_load: list[float],
    normalized_metrics: dict[str, list[float]],
    weekly_data: dict[str, list],
) -> dict:
    """Build the training load response dictionary.

    Args:
        dates: List of date strings
        daily_load: List of daily training hours
        normalized_metrics: Dictionary with normalized "tss", "ctl", "atl", "tsb"
        weekly_data: Dictionary with weekly volume data

    Returns:
        Complete response dictionary
    """
    return {
        "dates": dates,
        "daily_load": daily_load,
        "daily_tss": normalized_metrics["tss"],
        "ctl": normalized_metrics["ctl"],
        "atl": normalized_metrics["atl"],
        "tsb": normalized_metrics["tsb"],
        "weekly_dates": weekly_data["dates"],
        "weekly_volume": weekly_data["volume"],
        "weekly_rolling_avg": weekly_data["rolling_avg"],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


def _calculate_weekly_volume(dates: list[str], daily_load: list[float]) -> dict[str, list]:
    """Calculate weekly volume and 4-week rolling average."""
    if not dates:
        return {"volume": [], "dates": [], "rolling_avg": []}

    df_dict = {"date": dates, "load": daily_load}
    df_pd = pd.DataFrame(df_dict)
    df_pd["date"] = pd.to_datetime(df_pd["date"])
    df_pd["week"] = df_pd["date"].dt.to_period("W").dt.start_time
    weekly_df = df_pd.groupby("week")["load"].sum().reset_index()
    weekly_volume = weekly_df["load"].tolist()
    weekly_dates = [d.strftime("%Y-%m-%d") for d in weekly_df["week"].tolist()]

    # Calculate 4-week rolling average
    rolling_avg = []
    for i in range(len(weekly_volume)):
        start_idx = max(0, i - 3)
        avg = sum(weekly_volume[start_idx : i + 1]) / (i - start_idx + 1)
        rolling_avg.append(round(avg, 2))

    return {"volume": weekly_volume, "dates": weekly_dates, "rolling_avg": rolling_avg}


@router.get("/debug")
def debug_info():
    """Debug endpoint to verify database connection and configuration."""
    logger.info("Debug endpoint called")
    db = SessionLocal()  # pyright: ignore[reportGeneralTypeIssues]
    try:
        # Check table exists
        logger.debug("Checking database tables")
        # Database-agnostic table listing
        if "postgresql" in settings.database_url.lower() or "postgres" in settings.database_url.lower():
            # PostgreSQL
            tables = db.execute(
                text(
                    """
                    SELECT table_name as name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    """
                )
            ).fetchall()
        else:
            # SQLite
            tables = db.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()

        # Check activity count
        logger.debug("Counting activities")
        count = db.execute(text("SELECT COUNT(*) as cnt FROM activities")).fetchone()

        # Check sample data
        logger.debug("Fetching sample activities")
        sample = db.execute(text("SELECT id, start_time, duration_seconds FROM activities LIMIT 3")).fetchall()

        result = {
            "database_url": settings.database_url,
            "tables": [t[0] for t in tables],
            "activities_count": count[0] if count else 0,
            "sample_activities": [dict(r._mapping) for r in sample] if sample else [],
        }
        logger.info(f"Debug info retrieved: {count[0] if count else 0} activities found")
    except Exception as e:
        logger.error(f"Error in debug endpoint: {e}")
        raise
    else:
        return result
    finally:
        db.close()


@router.get("/training-load")
def training_load(days: int = 60, debug: bool = False, user_id: str = Depends(get_current_user_id)):
    """Get training load metrics (CTL, ATL, TSB, TSS) normalized to -100 to 100 scale.

    All metrics (TSS, ATL, CTL, TSB) are normalized to a -100 to 100 scale for consistent visualization.

    Args:
        days: Number of days to look back (default: 60)
        debug: If True, return raw query results for debugging
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Dictionary with:
        - dates: List of date strings (ISO format)
        - daily_load: List of daily training hours (raw values)
        - daily_tss: List of TSS values normalized to -100 to 100
        - ctl: List of CTL values normalized to -100 to 100
        - atl: List of ATL values normalized to -100 to 100
        - tsb: List of TSB values normalized to -100 to 100
        - weekly_dates: List of week start dates
        - weekly_volume: List of weekly volume hours
        - weekly_rolling_avg: List of 4-week rolling averages
        - last_updated: ISO timestamp

    Frontend Usage:
        GET /state/training-load?days=60

        Response structure:
        {
          "dates": ["2026-01-01", "2026-01-02", ...],
          "daily_tss": [-50.0, 25.0, ...],  // -100 to 100 scale
          "ctl": [-20.0, -15.0, ...],      // -100 to 100 scale
          "atl": [10.0, 15.0, ...],         // -100 to 100 scale
          "tsb": [-30.0, -30.0, ...],       // -100 to 100 scale
          ...
        }

        All metrics are aligned by index with the dates array.
    """
    logger.info(f"Training load requested: days={days}, debug={debug}, user_id={user_id}")
    db = SessionLocal()  # pyright: ignore[reportGeneralTypeIssues]

    if debug:
        logger.debug("Debug mode: fetching raw activity data")
        try:
            result = _get_debug_result(db, days)
            logger.debug(f"Debug result: {result}")
        except Exception as e:
            logger.exception(f"Error in debug mode: {e}")
            return {"debug": {"error": str(e), "message": "Failed to fetch debug data"}}
        else:
            return result
        finally:
            db.close()

    since_date = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    since_datetime = datetime.combine(since_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    logger.debug(f"Querying DailyTrainingLoad since: {since_datetime.isoformat()}")

    # Default empty response structure
    empty_response = {
        "dates": [],
        "daily_load": [],
        "daily_tss": [],
        "ctl": [],
        "atl": [],
        "tsb": [],
        "weekly_dates": [],
        "weekly_volume": [],
        "weekly_rolling_avg": [],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    try:
        # Read from DailyTrainingLoad table (single source of truth)
        daily_rows = db.execute(
            select(DailyTrainingLoad)
            .where(
                DailyTrainingLoad.user_id == user_id,
                DailyTrainingLoad.date >= since_datetime,
            )
            .order_by(DailyTrainingLoad.date)
        ).all()

        logger.info(f"Found {len(daily_rows)} daily training load records for user {user_id}")

        if not daily_rows:
            logger.info("No daily training load records found, returning empty response")
            return empty_response

        # Extract data from pre-computed DailyTrainingLoad table
        dates: list[str] = []
        daily_load: list[float] = []  # Daily training hours (we'll need to compute this from activities for now)
        daily_tss: list[float] = []  # Use load_score as TSS
        ctl_raw: list[float] = []
        atl_raw: list[float] = []
        tsb_raw: list[float] = []

        for row in daily_rows:
            daily_load_record = row[0]  # Extract the model instance from the Row object
            date_str = daily_load_record.date.date().isoformat()
            dates.append(date_str)
            daily_tss.append(daily_load_record.load_score)
            ctl_raw.append(daily_load_record.ctl)
            atl_raw.append(daily_load_record.atl)
            tsb_raw.append(daily_load_record.tsb)
            # For daily_load (hours), we'll approximate from load_score
            # A typical TSS of 100 = ~1 hour at FTP, so hours ≈ load_score / 100
            daily_load.append(daily_load_record.load_score / 100.0)

        # Normalize metrics to -100 to 100 scale
        normalized_metrics = {
            "tss": [_normalize_tss(tss) for tss in daily_tss],
            "ctl": [_normalize_metric(ctl, max_value=100.0) for ctl in ctl_raw],
            "atl": [_normalize_metric(atl, max_value=100.0) for atl in atl_raw],
            "tsb": _normalize_tsb_range(tsb_raw),
        }

        weekly_data = _calculate_weekly_volume(dates, daily_load)
        result = _build_training_load_response(dates, daily_load, normalized_metrics, weekly_data)

        tsb_norm_min = min(normalized_metrics["tsb"]) if normalized_metrics["tsb"] else 0.0
        tsb_norm_max = max(normalized_metrics["tsb"]) if normalized_metrics["tsb"] else 0.0
        logger.info(
            f"Training load read from DailyTrainingLoad: {len(dates)} days, "
            f"TSB normalized range: {tsb_norm_min:.1f} to {tsb_norm_max:.1f} "
            f"(all metrics on -100 to 100 scale)"
        )
    except Exception as e:
        logger.exception(f"Error reading training load from DailyTrainingLoad: {e}")
        # Return empty response instead of raising 500
        # Frontend can handle empty data gracefully
        return empty_response
    else:
        return result
    finally:
        db.close()


@router.get("/coach")
async def get_coach_insights(user_id: str = Depends(get_current_user_id)):
    """Get coaching insights from the LLM Coach.

    Returns:
        CoachAgentResponse with insights, recommendations, and risk assessment

    Rules:
        - Coach output comes ONLY from LLM service
        - Coach is gated by data quality
        - No rule-based logic here
    """
    request_time = time.time()
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    logger.info(f"[API] /state/coach endpoint called at {now_str}")
    logger.info("Coach insights requested")

    # Get overview data for coach
    try:
        overview = get_overview_data(user_id)
    except HTTPException:
        # Re-raise HTTPException as-is (e.g., 404 for no Strava account)
        raise
    except Exception as e:
        logger.error(f"Error getting overview for coach: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get training overview: {e!s}",
        ) from e

    # Get coach advice from LLM service (gated by data quality)
    try:
        coach_response = get_coach_advice(overview)
    except Exception as e:
        logger.exception(f"Error getting coach advice (error_type={type(e).__name__})")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate coaching insights: {type(e).__name__}: {e!s}",
        ) from e

    elapsed = time.time() - request_time
    logger.info(
        f"Coach response generated: risk_level={coach_response.get('risk_level')}, "
        f"intervention={coach_response.get('intervention')}, elapsed={elapsed:.3f}s"
    )
    return coach_response


def _build_limited_data_message(data_quality: str, activity_count: int) -> dict:
    """Build response message for limited data quality case.

    Args:
        data_quality: Data quality status
        activity_count: Number of activities

    Returns:
        Response dictionary for limited data quality
    """
    message = (
        "I have some of your training data, but there are gaps. I can provide "
        "limited insights, but more consistent data will improve my recommendations."
    )
    return {
        "summary": "Getting started with Virtus AI",
        "insights": [message],
        "recommendations": [
            "Keep your Strava activities synced",
            "Check back once you have 14+ days of training data",
        ],
        "risk_level": "none",
        "intervention": False,
        "follow_up_prompts": None,
        "data_quality": data_quality,
        "activity_count": activity_count,
    }


@router.get("/coach/initial")
def get_initial_coach_message(user_id: str = Depends(get_current_user_id)):
    """Get initial coach message for new users or users with insufficient data.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        Initial welcome message and guidance for users who just connected Strava
    """
    logger.info(f"Initial coach message requested for user_id={user_id}")

    try:
        overview = get_overview_data(user_id)
        data_quality = overview.get("data_quality", "insufficient")

        # Count activities for current user
        activity_count = 0
        try:
            with get_session() as db:
                result_count = db.execute(select(func.count(Activity.id)).where(Activity.user_id == user_id)).scalar()
                activity_count = result_count if result_count is not None else 0
        except Exception:
            # Fallback: count all activities
            with get_session() as db:
                result = db.execute(text("SELECT COUNT(*) FROM activities")).scalar()
                activity_count = result if result is not None else 0

        # Determine message based on data quality and activity count
        if data_quality == "ok":
            # Data quality is ok - redirect to regular coach
            return get_coach_insights()

        if data_quality == "insufficient":
            if activity_count == 0:
                message = (
                    "Welcome to Virtus AI! I'm syncing your Strava activities now. "
                    "Once I have at least 14 days of training data, I'll be able to provide "
                    "personalized coaching insights and recommendations."
                )
            elif activity_count < 10:
                message = (
                    f"Great! I've found {activity_count} activities. I'm still gathering your "
                    "training history. Once I have at least 14 days of consistent data, I'll "
                    "be able to analyze your training load and provide personalized guidance."
                )
            else:
                message = (
                    f"I'm analyzing your {activity_count} activities. I need a bit more data "
                    "to provide accurate insights. Keep training, and check back in a few days!"
                )
            return {
                "summary": "Getting started with Virtus AI",
                "insights": [message],
                "recommendations": [
                    "Keep your Strava activities synced",
                    "Check back once you have 14+ days of training data",
                ],
                "risk_level": "none",
                "intervention": False,
                "follow_up_prompts": None,
                "data_quality": data_quality,
                "activity_count": activity_count,
            }

        # Default case: Handle limited data quality (data_quality == "limited")
        # This is the fallback case when data_quality is neither "ok" nor "insufficient"
        return _build_limited_data_message(data_quality, activity_count)

    except Exception as e:
        logger.exception(f"Error getting initial coach message: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get initial coach message: {e!s}",
        ) from e
