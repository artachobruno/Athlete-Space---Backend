from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException
from loguru import logger
from sqlalchemy import text

from app.coach.service import run_coach_agent
from app.coach.state_builder import build_athlete_state
from app.core.settings import settings
from app.state.db import SessionLocal

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


def _calculate_training_load_metrics(daily_load: list[float]) -> dict[str, list[float]]:
    """Calculate CTL, ATL, and TSB from daily load."""

    def ewma(values, tau):
        alpha = 1 - pow(2.71828, -1 / tau)
        out = []
        prev = values[0] if values else 0
        for v in values:
            prev = alpha * v + (1 - alpha) * prev
            out.append(round(prev, 2))
        return out

    ctl = ewma(daily_load, tau=42)
    atl = ewma(daily_load, tau=7)
    tsb = [round(c - a, 2) for c, a in zip(ctl, atl, strict=False)]

    return {"ctl": ctl, "atl": atl, "tsb": tsb}


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

        # Calculate training load metrics
        metrics = _calculate_training_load_metrics(daily_load)

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
async def get_coach_insights(days: int = 60, days_to_race: int | None = None):
    """Get coaching insights from the Coach Agent.

    Args:
        days: Number of days to look back (default: 60)
        days_to_race: Optional days until next race

    Returns:
        CoachAgentResponse with insights, recommendations, and risk assessment
    """
    logger.info(f"Coach insights requested: days={days}, days_to_race={days_to_race}")

    # Get training load data
    training_data = training_load(days=days, debug=False)

    if not training_data.get("dates") or not training_data.get("ctl"):
        raise HTTPException(
            status_code=404,
            detail="Insufficient training data. Need at least some activities to generate insights.",
        )

    # Extract current metrics (most recent values) with explicit type narrowing
    ctl_list = list(training_data.get("ctl", []))
    atl_list = list(training_data.get("atl", []))
    tsb_list = list(training_data.get("tsb", []))
    daily_load_list = list(training_data.get("daily_load", []))
    dates_list = list(training_data.get("dates", []))

    if not ctl_list or not atl_list or not tsb_list:
        raise HTTPException(
            status_code=404,
            detail="Insufficient training data. Need at least some activities to generate insights.",
        )

    ctl_current = float(ctl_list[-1])
    atl_current = float(atl_list[-1])
    tsb_current = float(tsb_list[-1])

    # Build athlete state
    athlete_state = build_athlete_state(
        ctl=ctl_current,
        atl=atl_current,
        tsb=tsb_current,
        daily_load=[float(x) for x in daily_load_list],
        days_to_race=days_to_race,
    )

    # Run coach agent
    try:
        coach_response = run_coach_agent(athlete_state)
        logger.info(
            "Coach Agent response generated",
            risk_level=coach_response.risk_level,
            intervention=coach_response.intervention,
        )
        return coach_response.model_dump()

    except Exception as e:
        logger.error(f"Error running Coach Agent: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate coaching insights: {e!s}",
        ) from e
