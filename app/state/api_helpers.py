from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.db.models import DailyTrainingLoad, StravaAccount
from app.db.session import get_session
from app.state.errors import NoTrainingDataError


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
            user_id = result[0].user_id
            # Ensure we return a string (not UUID object) for compatibility with VARCHAR columns
            return str(user_id) if user_id is not None else None
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
        NoTrainingDataError: If no training data is available
    """
    since_date = datetime.now(timezone.utc).date() - timedelta(days=days)

    with get_session() as session:
        # Query DailyTrainingLoad table (pre-computed metrics)
        rows = session.execute(
            select(DailyTrainingLoad)
            .where(
                DailyTrainingLoad.user_id == user_id,
                DailyTrainingLoad.day >= since_date,
            )
            .order_by(DailyTrainingLoad.day)
        ).all()

        if not rows:
            raise NoTrainingDataError("No training data available")

        # Get actual daily training hours from daily_training_summary
        # This is more accurate than approximating from CTL
        daily_hours_result = session.execute(
            text("""
                SELECT
                    day,
                    (summary->>'load_score')::double precision AS load_score
                FROM daily_training_summary
                WHERE user_id = :user_id
                AND day >= :since_date
                ORDER BY day
            """),
            {"user_id": user_id, "since_date": since_date.isoformat()},
        ).fetchall()

        # Create a map of date -> actual daily hours
        daily_hours_map = {row[0]: (row[1] or 0.0) for row in daily_hours_result}

        # Extract data from pre-computed metrics
        dates: list[str] = []
        daily_load: list[float] = []
        ctl_values: list[float] = []
        atl_values: list[float] = []
        tsb_values: list[float] = []

        for row in rows:
            daily_load_record = row[0]
            date_str = daily_load_record.day.isoformat()
            date_obj = daily_load_record.day
            dates.append(date_str)

            # Use actual daily hours from daily_training_summary if available
            # Otherwise default to 0.0 (rest day)
            actual_hours = daily_hours_map.get(date_obj, 0.0)
            daily_load.append(actual_hours)

            ctl_values.append(daily_load_record.ctl or 0.0)
            atl_values.append(daily_load_record.atl or 0.0)
            tsb_values.append(daily_load_record.tsb or 0.0)

        # Get most recent values
        ctl = ctl_values[-1] if ctl_values else 0.0
        atl = atl_values[-1] if atl_values else 0.0
        tsb = tsb_values[-1] if tsb_values else 0.0

        return TrainingData(
            ctl=ctl,
            atl=atl,
            tsb=tsb,
            daily_load=daily_load,  # Actual daily training hours from daily_training_summary
            dates=dates,
        )
