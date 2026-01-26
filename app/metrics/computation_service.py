"""Metrics computation service for incremental recomputation.

Step 6: Efficiently recomputes metrics when new activities arrive.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.db.models import Activity, DailyTrainingLoad, WeeklyTrainingSummary
from app.db.session import get_session
from app.metrics.load_computation import (
    compute_ctl_atl_form_from_tss,
    compute_daily_tss_load,
)


def recompute_metrics_for_user(
    user_id: str,
    since_date: date | None = None,
) -> dict[str, int]:
    """Recompute metrics for a user.

    Args:
        user_id: User ID
        since_date: Optional date to recompute from (default: 42 days ago for CTL)

    Returns:
        Dictionary with counts of records created/updated
    """
    logger.info(f"[METRICS] Starting metrics recomputation for user_id={user_id}")

    with get_session() as session:
        # Determine date range
        if since_date is None:
            # Default: recompute last 42 days (CTL window) + buffer
            since_date = datetime.now(tz=timezone.utc).date() - timedelta(days=50)

        end_date = datetime.now(tz=timezone.utc).date()

        logger.info(f"[METRICS] Recomputing metrics for user_id={user_id} from {since_date.isoformat()} to {end_date.isoformat()}")

        # Fetch activities in date range
        activities = session.execute(
            select(Activity)
            .where(
                Activity.user_id == user_id,
                Activity.starts_at >= datetime.combine(since_date, datetime.min.time()).replace(tzinfo=timezone.utc),
                Activity.starts_at <= datetime.combine(end_date, datetime.max.time()).replace(tzinfo=timezone.utc),
            )
            .order_by(Activity.starts_at)
        ).all()

        activity_list = [a[0] for a in activities]
        logger.info(f"[METRICS] Found {len(activity_list)} activities for user_id={user_id}")

        if not activity_list:
            logger.info(f"[METRICS] No activities found for user_id={user_id}, skipping metrics computation")
            return {"daily_created": 0, "daily_skipped": 0, "weekly_created": 0, "weekly_updated": 0}

        # Compute daily TSS loads (unified metric from spec)
        daily_tss_loads = compute_daily_tss_load(activity_list, since_date, end_date)

        # Compute CTL, ATL, Form (FSB) from TSS
        metrics = compute_ctl_atl_form_from_tss(daily_tss_loads, since_date, end_date)

        # Update daily_training_load table
        # CRITICAL: EWMA (CTL/ATL) depends on initial conditions and previous values.
        # Once a day's metrics are written, they must NEVER change (immutable).
        # Historical overwrites corrupt the entire EWMA series silently.
        daily_created = 0
        daily_skipped = 0
        daily_updated = 0

        # Allow updates for recent days (last 14 days) to keep data current
        # Historical days (>14 days ago) are immutable to preserve EWMA integrity
        recent_cutoff = end_date - timedelta(days=14)

        for date_val in daily_tss_loads:
            metrics_for_date = metrics.get(date_val, {"ctl": 0.0, "atl": 0.0, "fsb": 0.0})

            # Check if record exists
            existing = session.execute(
                select(DailyTrainingLoad).where(
                    DailyTrainingLoad.user_id == user_id,
                    DailyTrainingLoad.day == date_val,
                )
            ).first()

            # Note: TSB column stores Form (FSB) value
            form_value = metrics_for_date.get("fsb", 0.0)
            ctl_val = metrics_for_date["ctl"]
            atl_val = metrics_for_date["atl"]

            if existing:
                # For recent days (within last 14 days), allow updates to keep data current
                if date_val >= recent_cutoff:
                    existing_record = existing[0]
                    existing_record.ctl = ctl_val
                    existing_record.atl = atl_val
                    existing_record.tsb = form_value
                    existing_record.updated_at = datetime.now(timezone.utc)
                    daily_updated += 1
                    logger.debug(
                        f"[METRICS] Updated recent day {date_val.isoformat()} for user_id={user_id} "
                        "(within 14-day update window)"
                    )
                else:
                    # For historical days, skip to preserve EWMA integrity
                    daily_skipped += 1
                    logger.debug(
                        f"[METRICS] Skipping historical day {date_val.isoformat()} for user_id={user_id} "
                        "(EWMA history must never change after write)"
                    )
                continue

            # Create new record (only for days that don't exist)
            daily_load = DailyTrainingLoad(
                user_id=user_id,
                day=date_val,
                ctl=ctl_val,
                atl=atl_val,
                tsb=form_value,  # Storing Form (FSB) in TSB column for backward compatibility
                load_model="default",
            )
            session.add(daily_load)
            daily_created += 1

        # Compute weekly summaries
        weekly_created = 0
        weekly_updated = 0

        # Group activities by week (Monday as week start)
        weekly_activities: dict[date, list[Activity]] = {}
        for activity in activity_list:
            activity_date = activity.start_time.date()
            # Get Monday of the week
            days_since_monday = activity_date.weekday()
            week_start = activity_date - timedelta(days=days_since_monday)

            if week_start not in weekly_activities:
                weekly_activities[week_start] = []
            weekly_activities[week_start].append(activity)

        # Compute weekly summaries
        for week_start, week_activities in weekly_activities.items():
            total_duration = sum((a.duration_seconds or 0) for a in week_activities)
            total_distance = sum((a.distance_meters or 0.0) for a in week_activities)
            total_elevation = sum((a.elevation_gain_meters or 0.0) for a in week_activities)
            activity_count = len(week_activities)

            # Compute intensity distribution (simplified: by activity type)
            type_distribution: dict[str, int] = {}
            for activity in week_activities:
                activity_type = activity.type or "unknown"
                type_distribution[activity_type] = type_distribution.get(activity_type, 0) + 1

            # Check if record exists
            existing = session.execute(
                select(WeeklyTrainingSummary).where(
                    WeeklyTrainingSummary.user_id == user_id,
                    WeeklyTrainingSummary.week_start == datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
                )
            ).first()

            if existing:
                # Update existing record
                weekly_summary = existing[0]
                weekly_summary.total_duration = total_duration
                weekly_summary.total_distance = total_distance
                weekly_summary.total_elevation = total_elevation
                weekly_summary.activity_count = activity_count
                weekly_summary.intensity_distribution = type_distribution
                weekly_summary.updated_at = datetime.now(timezone.utc)
                weekly_updated += 1
            else:
                # Create new record
                weekly_summary = WeeklyTrainingSummary(
                    user_id=user_id,
                    week_start=datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
                    total_duration=total_duration,
                    total_distance=total_distance,
                    total_elevation=total_elevation,
                    activity_count=activity_count,
                    intensity_distribution=type_distribution,
                )
                session.add(weekly_summary)
                weekly_created += 1

        # Commit all changes
        session.commit()

        logger.info(
            f"[METRICS] Metrics recomputation complete for user_id={user_id}: "
            f"daily_created={daily_created}, daily_updated={daily_updated}, daily_skipped={daily_skipped}, "
            f"weekly_created={weekly_created}, weekly_updated={weekly_updated}"
        )

        return {
            "daily_created": daily_created,
            "daily_updated": daily_updated,
            "daily_skipped": daily_skipped,
            "weekly_created": weekly_created,
            "weekly_updated": weekly_updated,
        }


def trigger_recompute_on_new_activities(user_id: str) -> None:
    """Trigger metrics recomputation when new activities arrive.

    This is called after activity ingestion to recompute metrics efficiently.

    Args:
        user_id: User ID
    """
    logger.info(f"[METRICS] Triggering recomputation for user_id={user_id} after new activities")

    # Recompute last 50 days (CTL window + buffer)
    since_date = datetime.now(tz=timezone.utc).date() - timedelta(days=50)

    try:
        result = recompute_metrics_for_user(user_id, since_date=since_date)
        logger.info(f"[METRICS] Recomputation triggered successfully: {result}")
    except Exception:
        logger.exception(f"[METRICS] Failed to recompute metrics for user_id={user_id}")
