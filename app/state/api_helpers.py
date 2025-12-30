from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.state.db import SessionLocal


@dataclass(slots=True)
class TrainingData:
    """Lightweight container for coach tools."""

    ctl: float
    atl: float
    tsb: float
    daily_load: list[float]
    dates: list[str]


def _ewma(values: list[float], tau: int) -> list[float]:
    alpha = 1 - pow(2.71828, -1 / tau)
    out: list[float] = []
    prev = values[0] if values else 0.0

    for v in values:
        prev = alpha * v + (1 - alpha) * prev
        out.append(round(prev, 2))

    return out


def get_training_data(days: int = 60) -> TrainingData:
    """Fetch and compute training metrics for coach tools."""
    db = SessionLocal()
    since = datetime.now(timezone.utc) - timedelta(days=days)

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
            {"since": since.isoformat()},
        ).fetchall()

        if not rows:
            raise RuntimeError("No training data available")

        dates = [str(r.day) for r in rows]
        daily_load = [float(r.hours) for r in rows]

        ctl_series = _ewma(daily_load, tau=42)
        atl_series = _ewma(daily_load, tau=7)
        tsb_series = [c - a for c, a in zip(ctl_series, atl_series, strict=False)]

        return TrainingData(
            ctl=ctl_series[-1],
            atl=atl_series[-1],
            tsb=tsb_series[-1],
            daily_load=daily_load,
            dates=dates,
        )

    finally:
        db.close()
