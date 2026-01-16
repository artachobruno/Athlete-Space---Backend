"""Training API endpoints with real data.

Step 6: Replaces mock data with real metrics from computed training load.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import select

from app.api.dependencies.auth import get_current_user_id
from app.api.schemas.schemas import (
    TrainingDistributionResponse,
    TrainingDistributionZone,
    TrainingSignal,
    TrainingSignalsResponse,
    TrainingStateMetrics,
    TrainingStateResponse,
)
from app.db.models import Activity, DailyTrainingLoad, WeeklyTrainingSummary
from app.db.session import get_session

router = APIRouter(prefix="/training", tags=["training"])


def _get_current_metrics(user_id: str) -> dict[str, float]:
    """Get current CTL, ATL, TSB for a user.

    Args:
        user_id: User ID

    Returns:
        Dictionary with ctl, atl, tsb values
    """
    with get_session() as session:
        # Get most recent daily load record
        today = datetime.now(tz=timezone.utc).date()
        result = session.execute(
            select(DailyTrainingLoad)
            .where(
                DailyTrainingLoad.user_id == user_id,
                DailyTrainingLoad.day <= today,
            )
            .order_by(DailyTrainingLoad.day.desc())
            .limit(1)
        ).first()

        if result:
            daily_load = result[0]
            return {
                "ctl": daily_load.ctl or 0.0,
                "atl": daily_load.atl or 0.0,
                "tsb": daily_load.tsb or 0.0,
            }

        # No metrics found - return zeros
        return {"ctl": 0.0, "atl": 0.0, "tsb": 0.0}


def _get_trend(ctl_values: list[float]) -> str:
    """Determine trend from CTL values.

    Args:
        ctl_values: List of CTL values (most recent last)

    Returns:
        "increasing" | "stable" | "decreasing"
    """
    if len(ctl_values) < 7:
        return "stable"

    recent_avg = sum(ctl_values[-7:]) / len(ctl_values[-7:])
    older_avg = sum(ctl_values[-14:-7]) / len(ctl_values[-14:-7]) if len(ctl_values) >= 14 else recent_avg

    diff = recent_avg - older_avg
    if diff > 2.0:
        return "increasing"
    if diff < -2.0:
        return "decreasing"
    return "stable"


@router.get("/state", response_model=TrainingStateResponse)
def get_training_state(user_id: str = Depends(get_current_user_id)):
    """Get current training state and metrics from computed data.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        TrainingStateResponse with current training state
    """
    logger.info(f"[TRAINING] GET /training/state called for user_id={user_id}")

    # Get current metrics
    current_metrics = _get_current_metrics(user_id)

    # Get trend from recent CTL values
    with get_session() as session:
        recent_ctl = session.execute(
            select(DailyTrainingLoad.ctl).where(DailyTrainingLoad.user_id == user_id).order_by(DailyTrainingLoad.day.desc()).limit(14)
        ).all()
        ctl_values = [r[0] or 0.0 for r in recent_ctl] if recent_ctl else [current_metrics["ctl"]]
        trend = _get_trend(ctl_values)

    # Get weekly volume and load
    today = datetime.now(tz=timezone.utc).date()
    days_since_monday = today.weekday()
    week_start = today - timedelta(days=days_since_monday)

    with get_session() as session:
        # Get this week's summary
        week_summary = session.execute(
            select(WeeklyTrainingSummary).where(
                WeeklyTrainingSummary.user_id == user_id,
                WeeklyTrainingSummary.week_start == datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            )
        ).first()

        week_volume_hours = 0.0
        week_load = 0.0

        if week_summary:
            ws = week_summary[0]
            week_volume_hours = ws.total_duration / 3600.0
            # Week load = approximate from CTL (CTL * 10 ≈ TSS)
            week_daily_loads = session.execute(
                select(DailyTrainingLoad.ctl).where(
                    DailyTrainingLoad.user_id == user_id,
                    DailyTrainingLoad.day >= week_start,
                    DailyTrainingLoad.day <= today,
                )
            ).all()
            week_load = sum((r[0] or 0.0) * 10.0 for r in week_daily_loads)

        # Get this month's summary
        month_start = today.replace(day=1)
        month_activities = session.execute(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.starts_at >= datetime.combine(month_start, datetime.min.time()).replace(tzinfo=timezone.utc),
                Activity.starts_at <= datetime.combine(today, datetime.max.time()).replace(tzinfo=timezone.utc),
            )
        ).all()

        month_volume_hours = sum(a[0].duration_seconds for a in month_activities) / 3600.0
        month_daily_loads = session.execute(
            select(DailyTrainingLoad.ctl).where(
                DailyTrainingLoad.user_id == user_id,
                DailyTrainingLoad.day >= month_start,
                DailyTrainingLoad.day <= today,
            )
        ).all()
        month_load = sum((r[0] or 0.0) * 10.0 for r in month_daily_loads)

    return TrainingStateResponse(
        current=TrainingStateMetrics(
            ctl=round(current_metrics["ctl"], 1),
            atl=round(current_metrics["atl"], 1),
            tsb=round(current_metrics["tsb"], 1),
            trend=trend,
        ),
        week_volume_hours=round(week_volume_hours, 1),
        week_load=round(week_load, 1),
        month_volume_hours=round(month_volume_hours, 1),
        month_load=round(month_load, 1),
        last_updated=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/distribution", response_model=TrainingDistributionResponse)
def get_training_distribution(period: str = "week", user_id: str = Depends(get_current_user_id)):
    """Get training distribution across zones and activity types from real data.

    Args:
        period: Time period: week | month | season (default: week)
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        TrainingDistributionResponse with distribution data
    """
    logger.info(f"[TRAINING] GET /training/distribution called for user_id={user_id}, period={period}")

    today = datetime.now(tz=timezone.utc).date()

    # Determine date range
    if period == "week":
        days_since_monday = today.weekday()
        start_date = today - timedelta(days=days_since_monday)
        end_date = today
    elif period == "month":
        start_date = today.replace(day=1)
        end_date = today
    elif period == "season":
        # Season = last 90 days
        start_date = today - timedelta(days=90)
        end_date = today
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid period: {period}. Must be 'week', 'month', or 'season'.",
        )

    with get_session() as session:
        # Get activities in period
        activities = session.execute(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.starts_at >= datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc),
                Activity.starts_at <= datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc),
            )
        ).all()

        activity_list = [a[0] for a in activities]

        # Calculate total hours
        total_hours = sum(a.duration_seconds for a in activity_list) / 3600.0

        # Simplified zone distribution (based on activity type and duration)
        zone_hours = [0.0, 0.0, 0.0, 0.0, 0.0]

        for activity in activity_list:
            duration_hours = activity.duration_seconds / 3600.0
            activity_type = activity.type.lower()

            # Simplified zone assignment based on activity characteristics
            if activity_type in {"run", "trail run"}:
                if duration_hours > 1.5:
                    zone_hours[0] += duration_hours * 0.6
                    zone_hours[1] += duration_hours * 0.4
                elif duration_hours > 0.75:
                    zone_hours[1] += duration_hours * 0.7
                    zone_hours[2] += duration_hours * 0.3
                else:
                    zone_hours[2] += duration_hours * 0.5
                    zone_hours[3] += duration_hours * 0.5
            elif activity_type in {"ride", "virtualride"}:
                if duration_hours > 2.0:
                    zone_hours[0] += duration_hours * 0.5
                    zone_hours[1] += duration_hours * 0.5
                else:
                    zone_hours[1] += duration_hours * 0.6
                    zone_hours[2] += duration_hours * 0.4
            else:
                zone_hours[0] += duration_hours * 0.5
                zone_hours[1] += duration_hours * 0.5

        # Calculate percentages
        zone_percentages = [round((h / total_hours * 100) if total_hours > 0 else 0, 1) for h in zone_hours]

        zones = [
            TrainingDistributionZone(zone="Zone 1", hours=round(zone_hours[0], 1), percentage=zone_percentages[0]),
            TrainingDistributionZone(zone="Zone 2", hours=round(zone_hours[1], 1), percentage=zone_percentages[1]),
            TrainingDistributionZone(zone="Zone 3", hours=round(zone_hours[2], 1), percentage=zone_percentages[2]),
            TrainingDistributionZone(zone="Zone 4", hours=round(zone_hours[3], 1), percentage=zone_percentages[3]),
            TrainingDistributionZone(zone="Zone 5", hours=round(zone_hours[4], 1), percentage=zone_percentages[4]),
        ]

        # Activity type distribution
        by_type: dict[str, float] = {}
        for activity in activity_list:
            activity_type = activity.type
            duration_hours = activity.duration_seconds / 3600.0
            by_type[activity_type] = by_type.get(activity_type, 0.0) + duration_hours

        by_type = {k: round(v, 1) for k, v in by_type.items()}

        return TrainingDistributionResponse(
            period=period,
            total_hours=round(total_hours, 1),
            zones=zones,
            by_type=by_type,
        )


@router.get("/signals", response_model=TrainingSignalsResponse)
def get_training_signals(user_id: str = Depends(get_current_user_id)):
    """Get training signals and observations from computed metrics.

    Args:
        user_id: Current authenticated user ID (from auth dependency)

    Returns:
        TrainingSignalsResponse with active training signals
    """
    logger.info(f"[TRAINING] GET /training/signals called for user_id={user_id}")

    now = datetime.now(timezone.utc)

    # Get current metrics
    current_metrics = _get_current_metrics(user_id)
    ctl = current_metrics["ctl"]
    atl = current_metrics["atl"]
    tsb = current_metrics["tsb"]

    signals: list[TrainingSignal] = []

    # Signal 1: TSB-based recovery signal
    if tsb > 5:
        signals.append(
            TrainingSignal(
                id=f"signal_{user_id[:8]}_tsb_positive",
                type="readiness",
                severity="low",
                message="TSB is positive, indicating good recovery and readiness for training",
                timestamp=now.isoformat(),
                metrics={"tsb": round(tsb, 1), "ctl": round(ctl, 1)},
            )
        )
    elif tsb < -10:
        signals.append(
            TrainingSignal(
                id=f"signal_{user_id[:8]}_tsb_negative",
                type="fatigue",
                severity="high",
                message="TSB is very negative, indicating high fatigue. Consider recovery.",
                timestamp=now.isoformat(),
                metrics={"tsb": round(tsb, 1), "atl": round(atl, 1)},
            )
        )
    elif tsb < -5:
        signals.append(
            TrainingSignal(
                id=f"signal_{user_id[:8]}_tsb_moderate",
                type="fatigue",
                severity="moderate",
                message="TSB is negative, indicating accumulated fatigue. Monitor recovery.",
                timestamp=now.isoformat(),
                metrics={"tsb": round(tsb, 1), "atl": round(atl, 1)},
            )
        )

    # Signal 2: CTL trend
    with get_session() as session:
        recent_ctl = session.execute(
            select(DailyTrainingLoad.ctl).where(DailyTrainingLoad.user_id == user_id).order_by(DailyTrainingLoad.day.desc()).limit(14)
        ).all()
        ctl_values = [r[0] or 0.0 for r in recent_ctl] if recent_ctl else []

        if len(ctl_values) >= 7:
            recent_avg = sum(ctl_values[:7]) / 7
            older_avg = sum(ctl_values[7:14]) / 7 if len(ctl_values) >= 14 else recent_avg
            trend_diff = recent_avg - older_avg

            if trend_diff > 3:
                signals.append(
                    TrainingSignal(
                        id=f"signal_{user_id[:8]}_ctl_rising",
                        type="readiness",
                        severity="moderate",
                        message="CTL is trending upward - maintain consistency to build fitness",
                        timestamp=now.isoformat(),
                        metrics={"ctl": round(ctl, 1), "trend": round(trend_diff, 1)},
                    )
                )
            elif trend_diff < -3:
                signals.append(
                    TrainingSignal(
                        id=f"signal_{user_id[:8]}_ctl_falling",
                        type="undertraining",
                        severity="moderate",
                        message="CTL is trending downward - consider increasing training load",
                        timestamp=now.isoformat(),
                        metrics={"ctl": round(ctl, 1), "trend": round(trend_diff, 1)},
                    )
                )

    # Generate summary and recommendation
    if not signals:
        summary = "Training load is balanced. Continue current training volume and intensity."
        recommendation = "Maintain current training plan."
    elif tsb > 5:
        summary = "Good recovery state with positive TSB. Ready for quality training."
        recommendation = "Consider adding intensity work while maintaining recovery."
    elif tsb < -10:
        summary = "High fatigue detected. Recovery should be prioritized."
        recommendation = "Reduce training load and focus on recovery activities."
    else:
        summary = "Training load is manageable but monitor fatigue levels."
        recommendation = "Continue training with attention to recovery."

    return TrainingSignalsResponse(
        signals=signals,
        summary=summary,
        recommendation=recommendation,
    )
