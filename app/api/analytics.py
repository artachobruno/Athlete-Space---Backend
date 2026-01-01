from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from loguru import logger
from sqlalchemy import text

from app.metrics.training_load import calculate_ctl_atl_tsb
from app.state.db import SessionLocal

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/metrics")
def metrics(days: int = 60, athlete_id: int = 23078584):
    """Get training metrics (CTL, ATL, TSB) with daily aggregations for charting.

    Args:
        days: Number of days to look back (default: 60)
        athlete_id: Athlete ID (default: 23078584)

    Returns:
        Dictionary with "chart" key containing list of daily metric objects
    """
    logger.info("Analytics metrics requested", days=days, athlete_id=athlete_id)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_str = since.isoformat()

    db = SessionLocal()
    try:
        # Query daily aggregations
        rows = db.execute(
            text(
                """
                SELECT
                    date(start_time) as day,
                    SUM(duration_s) / 60.0 as duration_min,
                    SUM(distance_m) / 1000.0 as distance_km,
                    AVG(avg_hr) as hr_avg,
                    SUM(duration_s) / 3600.0 as hours
                FROM activities
                WHERE start_time >= :since
                  AND athlete_id = :athlete_id
                GROUP BY day
                ORDER BY day
                """
            ),
            {"since": since_str, "athlete_id": athlete_id},
        ).fetchall()

        if not rows:
            logger.info("No activity data found for analytics")
            return {"chart": []}

        # Extract data
        _dates = [str(r.day) for r in rows]
        daily_load = [float(r.hours) for r in rows]

        # Calculate CTL/ATL/TSB series
        metrics_series = calculate_ctl_atl_tsb(daily_load)
        ctl_series = metrics_series["ctl"]
        atl_series = metrics_series["atl"]
        tsb_series = metrics_series["tsb"]

        # Build chart data
        chart_data = []
        for i, row in enumerate(rows):
            chart_data.append({
                "date": str(row.day),
                "CTL": float(ctl_series[i]) if i < len(ctl_series) else 0.0,
                "ATL": float(atl_series[i]) if i < len(atl_series) else 0.0,
                "TSB": float(tsb_series[i]) if i < len(tsb_series) else 0.0,
                "hr": float(row.hr_avg) if row.hr_avg is not None else None,
                "dist": float(row.distance_km) if row.distance_km is not None else 0.0,
                "time": float(row.duration_min) if row.duration_min is not None else 0.0,
            })

        logger.info(f"Analytics metrics calculated: {len(chart_data)} days")
    except Exception as e:
        logger.error(f"Error calculating analytics metrics: {e}", exc_info=True)
        raise
    else:
        return {"chart": chart_data}
    finally:
        db.close()
