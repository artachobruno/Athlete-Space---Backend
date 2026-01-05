from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.state.db import get_session
from app.state.models import DailyTrainingLoad, StravaAccount


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
    """Fetch training metrics from computed DailyTrainingLoad table.

    Uses pre-computed DTL (Daily Training Load) values that are normalized
    across all sports using the improved load computation model.

    Args:
        user_id: Clerk user ID (string) to filter metrics
        days: Number of days to look back (default: 60)

    Returns:
        TrainingData with CTL, ATL, TSB metrics from computed DTL

    Raises:
        RuntimeError: If no training data is available
    """
    since_date = datetime.now(timezone.utc).date() - timedelta(days=days)

    with get_session() as session:
        # Query DailyTrainingLoad table (pre-computed metrics)
        rows = session.execute(
            select(DailyTrainingLoad)
            .where(
                DailyTrainingLoad.user_id == user_id,
                DailyTrainingLoad.date >= datetime.combine(since_date, datetime.min.time()).replace(tzinfo=timezone.utc),
            )
            .order_by(DailyTrainingLoad.date)
        ).all()

        if not rows:
            raise RuntimeError("No training data available")

        # Extract data from pre-computed metrics
        dates: list[str] = []
        daily_load: list[float] = []
        ctl_values: list[float] = []
        atl_values: list[float] = []
        tsb_values: list[float] = []

        for row in rows:
            daily_load_record = row[0]
            dates.append(daily_load_record.date.date().isoformat())
            # Use pre-computed DTL (load_score) - normalized across all sports
            daily_load.append(daily_load_record.load_score)
            ctl_values.append(daily_load_record.ctl)
            atl_values.append(daily_load_record.atl)
            tsb_values.append(daily_load_record.tsb)

        # Get most recent values
        ctl = ctl_values[-1] if ctl_values else 0.0
        atl = atl_values[-1] if atl_values else 0.0
        tsb = tsb_values[-1] if tsb_values else 0.0

        return TrainingData(
            ctl=ctl,
            atl=atl,
            tsb=tsb,
            daily_load=daily_load,  # DTL values (normalized, not raw hours)
            dates=dates,
        )
