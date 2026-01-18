"""Read-only access to training metrics.

Snapshot of training state (CTL, ATL, TSB).
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Activity, DailyTrainingLoad, WeeklyTrainingSummary
from app.db.session import get_session
from app.tools.interfaces import TrainingMetrics


def get_training_metrics(
    user_id: str,
    as_of: date,
) -> TrainingMetrics:
    """Get training metrics snapshot.

    READ-ONLY: Snapshot of training state.
    Uses precomputed metrics - does NOT recompute.

    Args:
        user_id: User ID
        as_of: Date to get metrics for

    Returns:
        TrainingMetrics with CTL, ATL, TSB, and weekly_load
    """
    logger.debug(f"Reading training metrics: user_id={user_id}, as_of={as_of}")

    with get_session() as session:
        # Get latest DailyTrainingLoad entry for or before as_of
        load_query = (
            select(DailyTrainingLoad)
            .where(
                DailyTrainingLoad.user_id == user_id,
                DailyTrainingLoad.day <= as_of,
            )
            .order_by(DailyTrainingLoad.day.desc())
            .limit(1)
        )
        load_result = session.execute(load_query).first()
        load = load_result[0] if load_result else None

        # Get CTL, ATL, TSB from DailyTrainingLoad
        ctl = load.ctl if load and load.ctl is not None else 0.0
        atl = load.atl if load and load.atl is not None else 0.0
        tsb = load.tsb if load and load.tsb is not None else 0.0

        # Calculate weekly_load from activities in the week containing as_of
        week_start = as_of - timedelta(days=as_of.weekday())  # Monday
        week_end = week_start + timedelta(days=6)  # Sunday

        week_start_datetime = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        week_end_datetime = datetime.combine(week_end, datetime.max.time()).replace(tzinfo=timezone.utc)

        # Sum TSS from activities in this week
        activities_query = select(Activity).where(
            Activity.user_id == user_id,
            Activity.starts_at >= week_start_datetime,
            Activity.starts_at <= week_end_datetime,
        )
        activities = list(session.execute(activities_query).scalars().all())

        weekly_load = sum(act.tss if act.tss is not None else 0.0 for act in activities)

        return TrainingMetrics(
            ctl=ctl,
            atl=atl,
            tsb=tsb,
            weekly_load=weekly_load,
        )
