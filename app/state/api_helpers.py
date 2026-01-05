from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.metrics.training_load import calculate_ctl_atl_tsb
from app.state.db import SessionLocal, get_session
from app.state.models import StravaAccount


@dataclass(slots=True)
class TrainingData:
    """Lightweight container for coach tools."""

    ctl: float
    atl: float
    tsb: float
    daily_load: list[float]
    dates: list[str]


def get_user_id_from_athlete_id(athlete_id: int) -> str | None:
    """Get user_id from athlete_id using StravaAccount.

    Args:
        athlete_id: Strava athlete ID (int)

    Returns:
        User ID (string) or None if not found
    """
    with get_session() as session:
        result = session.execute(select(StravaAccount).where(StravaAccount.athlete_id == str(athlete_id))).first()
        if result:
            return result[0].user_id
        return None


def get_training_data(user_id: str, days: int = 60) -> TrainingData:
    """Fetch and compute training metrics for coach tools.

    Args:
        user_id: Clerk user ID (string) to filter activities
        days: Number of days to look back (default: 60)

    Returns:
        TrainingData with CTL, ATL, TSB metrics

    Raises:
        RuntimeError: If no training data is available
    """
    db = SessionLocal()
    since = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        rows = db.execute(
            text(
                """
                SELECT
                    date(start_time) as day,
                    SUM(duration_seconds) / 3600.0 as hours
                FROM activities
                WHERE user_id = :user_id
                AND start_time >= :since
                GROUP BY day
                ORDER BY day
                """
            ),
            {
                "user_id": user_id,
                "since": since.isoformat(),
            },
        ).fetchall()

        if not rows:
            raise RuntimeError("No training data available")

        dates = [str(r.day) for r in rows]
        daily_load = [float(r.hours) if r.hours is not None else 0.0 for r in rows]

        # Use canonical metrics computation
        metrics = calculate_ctl_atl_tsb(daily_load)
        ctl_series = metrics["ctl"]
        atl_series = metrics["atl"]
        tsb_series = metrics["tsb"]

        return TrainingData(
            ctl=ctl_series[-1] if ctl_series else 0.0,
            atl=atl_series[-1] if atl_series else 0.0,
            tsb=tsb_series[-1] if tsb_series else 0.0,
            daily_load=daily_load,
            dates=dates,
        )

    finally:
        db.close()
