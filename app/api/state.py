import time
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException
from loguru import logger
from sqlalchemy import text

from app.api.me import get_overview
from app.coach.coach_service import get_coach_advice
from app.core.settings import settings
from app.metrics.training_load import calculate_ctl_atl_tsb
from app.state.db import SessionLocal, get_session
from app.state.models import Activity

router = APIRouter(prefix="/state", tags=["state"])


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
    db = SessionLocal()
    try:
        # Check table exists
        logger.debug("Checking database tables")
        tables = db.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()

        # Check activity count
        logger.debug("Counting activities")
        count = db.execute(text("SELECT COUNT(*) as cnt FROM activities")).fetchone()

        # Check sample data
        logger.debug("Fetching sample activities")
        sample = db.execute(text("SELECT activity_id, start_time, duration_s FROM activities LIMIT 3")).fetchall()

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
def training_load(days: int = 60, debug: bool = False):
    """Get training load metrics (CTL, ATL, TSB).

    Args:
        days: Number of days to look back (default: 60)
        debug: If True, return raw query results for debugging
    """
    logger.info(f"Training load requested: days={days}, debug={debug}")
    db = SessionLocal()

    # Debug: First check if we can connect and see any data at all
    if debug:
        logger.debug("Debug mode: fetching raw activity data")
        total_count = db.execute(text("SELECT COUNT(*) as cnt FROM activities")).fetchone()
        sample_rows = db.execute(text("SELECT activity_id, start_time, duration_s FROM activities LIMIT 5")).fetchall()
        result = {
            "debug": {
                "total_activities": total_count[0] if total_count else 0,
                "sample_rows": [dict(r._mapping) for r in sample_rows] if sample_rows else [],
                "query_filter": f"start_time >= {datetime.now(timezone.utc) - timedelta(days=days)}",
            }
        }
        logger.debug(f"Debug result: {result}")
        return result

    since = datetime.now(timezone.utc) - timedelta(days=days)
    # Convert to ISO format string for SQLite compatibility
    since_str = since.isoformat()
    logger.debug(f"Querying activities since: {since_str}")

    try:
        rows = db.execute(
            text(
                """
                SELECT
                    date(start_time) as day,
                    SUM(duration_s) / 3600.0 as hours
                FROM activities
                WHERE start_time >= :since
                GROUP BY day
                ORDER BY day
                """
            ),
            {"since": since_str},
        ).fetchall()

        logger.info(f"Found {len(rows)} days with activity data")

        dates = [r.day for r in rows]
        daily_load = [r.hours for r in rows]

        # Calculate training load metrics using canonical computation rules
        metrics = calculate_ctl_atl_tsb(daily_load)

        # Calculate weekly volume for bar chart
        weekly_data = _calculate_weekly_volume(dates, daily_load)

        result = {
            "dates": dates,
            "daily_load": daily_load,
            "ctl": metrics["ctl"],
            "atl": metrics["atl"],
            "tsb": metrics["tsb"],
            "weekly_dates": weekly_data["dates"],
            "weekly_volume": weekly_data["volume"],
            "weekly_rolling_avg": weekly_data["rolling_avg"],
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        tsb_list = metrics["tsb"]
        tsb_min = min(tsb_list) if tsb_list else 0.0
        tsb_max = max(tsb_list) if tsb_list else 0.0
        logger.info(f"Training load calculated: {len(dates)} days, TSB range: {tsb_min:.1f} to {tsb_max:.1f}")
    except Exception as e:
        logger.error(f"Error calculating training load: {e}")
        raise
    else:
        return result
    finally:
        db.close()


@router.get("/coach")
async def get_coach_insights():
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

    # Get overview from /me/overview endpoint
    try:
        overview = get_overview()
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
        logger.error(f"Error getting coach advice: {type(e).__name__}: {e}", exc_info=True)
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
def get_initial_coach_message():
    """Get initial coach message for new users or users with insufficient data.

    Returns:
        Initial welcome message and guidance for users who just connected Strava
    """
    logger.info("Initial coach message requested")

    try:
        overview = get_overview()
        data_quality = overview.get("data_quality", "insufficient")
        activity_count = 0

        # Count activities
        with get_session() as db:
            activity_count = db.query(Activity).count()

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
        logger.error(f"Error getting initial coach message: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get initial coach message: {e!s}",
        ) from e
