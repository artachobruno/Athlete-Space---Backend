"""Background job to compute weekly report metrics.

Computes summary_score, activities_completed, and adherence_percentage
for weekly reports based on planned sessions and actual activities.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import func, select

from app.db.models import Activity, PlannedSession, StravaAccount, WeeklyIntent, WeeklyReport, WeeklyTrainingSummary
from app.db.session import get_session


def compute_weekly_report_metrics(athlete_id: int, week_start: datetime) -> dict[str, float | int | None]:
    """Compute metrics for a weekly report.

    Args:
        athlete_id: Athlete ID
        week_start: Week start date (Monday)

    Returns:
        Dictionary with summary_score, activities_completed, adherence_percentage
    """
    week_end = week_start + timedelta(days=6)

    with get_session() as session:
        # Get user_id from athlete_id (via StravaAccount or PlannedSession)
        user_id_result = session.execute(select(PlannedSession.user_id).where(PlannedSession.athlete_id == athlete_id).limit(1)).first()

        if not user_id_result:
            logger.warning(f"No user_id found for athlete_id={athlete_id}, skipping metrics computation")
            return {
                "summary_score": None,
                "activities_completed": None,
                "adherence_percentage": None,
            }

        user_id = user_id_result[0]

        # Get planned sessions for the week
        planned_sessions = (
            session.execute(
                select(PlannedSession).where(
                    PlannedSession.user_id == user_id,
                    PlannedSession.athlete_id == athlete_id,
                    PlannedSession.date >= week_start,
                    PlannedSession.date <= week_end,
                    PlannedSession.status != "cancelled",
                )
            )
            .scalars()
            .all()
        )

        # Get completed activities for the week
        completed_activities = (
            session.execute(
                select(Activity).where(
                    Activity.user_id == user_id,
                    Activity.start_time >= week_start,
                    Activity.start_time <= week_end.replace(hour=23, minute=59, second=59),
                )
            )
            .scalars()
            .all()
        )

        # Get weekly training summary if available
        weekly_summary = session.execute(
            select(WeeklyTrainingSummary).where(
                WeeklyTrainingSummary.user_id == user_id,
                WeeklyTrainingSummary.week_start == week_start,
            )
        ).scalar_one_or_none()

        # Count planned sessions (excluding rest days)
        planned_count = len([s for s in planned_sessions if s.status != "rest"])
        activities_completed = len(completed_activities)

        # Calculate adherence percentage
        # Adherence = (completed activities / planned sessions) * 100
        # If no planned sessions, use weekly summary activity count as baseline
        adherence_percentage = None
        if planned_count > 0:
            adherence_percentage = min(100.0, (activities_completed / planned_count) * 100.0)
        elif weekly_summary and weekly_summary.activity_count > 0:
            # If no planned sessions but we have activities, consider it 100% (no plan to adhere to)
            adherence_percentage = 100.0

        # Compute summary score (0-10 scale)
        # Factors:
        # - Adherence (40%): How well did they follow the plan
        # - Volume completion (30%): Did they hit volume targets
        # - Consistency (30%): Did they train consistently throughout the week
        summary_score = None

        if adherence_percentage is not None:
            # Base score from adherence (0-4 points)
            adherence_score = (adherence_percentage / 100.0) * 4.0

            # Volume score (0-3 points)
            volume_score = 0.0
            if weekly_summary:
                # Get weekly intent to compare target vs actual (avoid circular import by querying directly)
                intent_model = session.execute(
                    select(WeeklyIntent)
                    .where(
                        WeeklyIntent.athlete_id == athlete_id,
                        WeeklyIntent.week_start == week_start,
                        WeeklyIntent.is_active.is_(True),
                    )
                    .order_by(WeeklyIntent.version.desc())
                ).scalar_one_or_none()
                if intent_model and intent_model.target_volume_hours:
                    actual_hours = weekly_summary.total_duration / 3600.0
                    target_hours = intent_model.target_volume_hours
                    if target_hours > 0:
                        volume_ratio = min(1.0, actual_hours / target_hours)
                        volume_score = volume_ratio * 3.0
                # No target, give partial credit for any training
                elif weekly_summary.total_duration > 0:
                    volume_score = 1.5  # Partial credit

            # Consistency score (0-3 points)
            # Check if activities are spread throughout the week
            consistency_score = 0.0
            if activities_completed > 0:
                activity_dates = {a.start_time.date() for a in completed_activities}
                days_trained = len(activity_dates)
                # Ideal: 5-6 days of training per week
                if days_trained >= 5:
                    consistency_score = 3.0
                elif days_trained >= 4:
                    consistency_score = 2.0
                elif days_trained >= 3:
                    consistency_score = 1.0
                # Less than 3 days gets 0

            summary_score = adherence_score + volume_score + consistency_score

        logger.info(
            f"Computed weekly report metrics for athlete_id={athlete_id}, week_start={week_start.date()}: "
            f"summary_score={summary_score}, activities_completed={activities_completed}, "
            f"adherence_percentage={adherence_percentage}"
        )

        return {
            "summary_score": summary_score,
            "activities_completed": activities_completed,
            "adherence_percentage": adherence_percentage,
        }


def update_weekly_report_metrics(athlete_id: int, week_start: datetime) -> None:
    """Update metrics for an existing weekly report.

    Args:
        athlete_id: Athlete ID
        week_start: Week start date (Monday)
    """
    with get_session() as session:
        report = session.execute(
            select(WeeklyReport)
            .where(
                WeeklyReport.athlete_id == athlete_id,
                WeeklyReport.week_start == week_start,
                WeeklyReport.is_active.is_(True),
            )
            .order_by(WeeklyReport.version.desc())
        ).scalar_one_or_none()

        if not report:
            logger.debug(f"No active weekly report found for athlete_id={athlete_id}, week_start={week_start.date()}")
            return

        metrics = compute_weekly_report_metrics(athlete_id, week_start)

        report.summary_score = metrics["summary_score"]
        report.activities_completed = metrics["activities_completed"]
        report.adherence_percentage = metrics["adherence_percentage"]
        report.updated_at = datetime.now(timezone.utc)

        session.commit()

        logger.info(f"Updated weekly report metrics for report_id={report.id}, athlete_id={athlete_id}, week_start={week_start.date()}")


def update_all_recent_weekly_reports(athlete_id: int, weeks: int = 4) -> None:
    """Update metrics for all recent weekly reports.

    Args:
        athlete_id: Athlete ID
        weeks: Number of recent weeks to update (default: 4)
    """
    today = datetime.now(timezone.utc).date()
    days_since_monday = today.weekday()
    current_week_start = datetime.combine(today - timedelta(days=days_since_monday), datetime.min.time()).replace(tzinfo=timezone.utc)

    for i in range(weeks):
        week_start = current_week_start - timedelta(weeks=i)
        try:
            update_weekly_report_metrics(athlete_id, week_start)
        except Exception:
            logger.exception(
                f"Failed to update weekly report metrics for athlete_id={athlete_id}, week_start={week_start.date()}"
            )


def update_all_recent_weekly_reports_for_all_users() -> None:
    """Update metrics for all users' recent weekly reports.

    This is called by the scheduler to update metrics for all active users.
    """
    logger.info("Starting weekly report metrics update for all users")

    with get_session() as session:
        # Get all active Strava accounts
        accounts = session.execute(select(StravaAccount)).scalars().all()

        updated_count = 0
        error_count = 0

        for account in accounts:
            try:
                athlete_id = int(account.athlete_id)
                update_all_recent_weekly_reports(athlete_id, weeks=4)
                updated_count += 1
            except Exception:
                logger.exception(
                    f"Failed to update weekly report metrics for athlete_id={account.athlete_id}"
                )
                error_count += 1

        logger.info(f"Weekly report metrics update complete: updated={updated_count}, errors={error_count}")
