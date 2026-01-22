"""Context builder for daily decision generation.

Builds structured context from athlete data for LLM-based daily decision generation.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.calendar.training_summary import build_training_summary
from app.coach.schemas.intent_schemas import DailyDecision, SeasonPlan, WeeklyIntent
from app.coach.utils.reconciliation_context import get_recent_missed_workouts, get_reconciliation_stats
from app.db.models import Activity, PlannedSession
from app.db.session import get_session
from app.services.intelligence.store import IntentStore
from app.services.overview_service import get_overview_data


def _normalize_datetime(dt: datetime) -> datetime:
    """Normalize datetime to timezone-aware (UTC).

    Args:
        dt: Datetime that may be naive or aware

    Returns:
        Timezone-aware datetime (UTC)
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _build_athlete_state_from_overview(overview: dict[str, Any]) -> dict[str, Any]:
    """Build athlete state dict from overview data.

    Args:
        overview: Overview data from /me/overview

    Returns:
        Athlete state dictionary
    """
    today_metrics = overview.get("today", {})
    metrics = overview.get("metrics", {})

    # Get latest CTL, ATL, TSB
    ctl_list = metrics.get("ctl", [])
    atl_list = metrics.get("atl", [])
    tsb_list = metrics.get("tsb", [])

    ctl = today_metrics.get("ctl", ctl_list[-1][1] if ctl_list else 0.0)
    atl = today_metrics.get("atl", atl_list[-1][1] if atl_list else 0.0)
    tsb = today_metrics.get("tsb", tsb_list[-1][1] if tsb_list else 0.0)

    # Calculate trends
    ctl_trend = "stable"
    if len(ctl_list) >= 7:
        recent_avg = sum(val for _, val in ctl_list[-7:]) / 7
        prev_avg = sum(val for _, val in ctl_list[-14:-7]) / 7 if len(ctl_list) >= 14 else recent_avg
        if recent_avg > prev_avg * 1.02:
            ctl_trend = "rising"
        elif recent_avg < prev_avg * 0.98:
            ctl_trend = "falling"

    # ATL trend calculation (for future use)
    _atl_trend = "stable"
    if len(atl_list) >= 7:
        recent_avg = sum(val for _, val in atl_list[-7:]) / 7
        prev_avg = sum(val for _, val in atl_list[-14:-7]) / 7 if len(atl_list) >= 14 else recent_avg
        if recent_avg > prev_avg * 1.02:
            _atl_trend = "rising"
        elif recent_avg < prev_avg * 0.98:
            _atl_trend = "falling"

    # Calculate 7-day and 14-day volume from activities
    seven_day_volume = today_metrics.get("seven_day_volume_hours", 0.0)
    fourteen_day_volume = today_metrics.get("fourteen_day_volume_hours", 0.0)

    # Build flags
    flags = []
    if tsb < -10:
        flags.append("high_fatigue")
    elif tsb > 10:
        flags.append("fresh")
    if ctl_trend == "falling":
        flags.append("fitness_declining")

    return {
        "ctl": float(ctl),
        "atl": float(atl),
        "tsb": float(tsb),
        "load_trend": ctl_trend,
        "volatility": "low",  # Simplified
        "days_since_rest": 0,  # Would need activity analysis
        "seven_day_volume_hours": float(seven_day_volume),
        "fourteen_day_volume_hours": float(fourteen_day_volume),
        "confidence": 0.8 if overview.get("data_quality") == "ok" else 0.5,
        "flags": flags,
    }


def _format_training_history_from_summary(summary) -> str:
    """Format training history from TrainingSummary.

    Args:
        summary: TrainingSummary object

    Returns:
        Formatted training history string
    """
    volume = summary.volume
    sessions_completed = volume.get("sessions_completed", 0)
    total_duration_hours = volume.get("total_duration_minutes", 0) / 60.0

    if sessions_completed == 0:
        return f"No activities in the last {summary.days} days"

    return f"Last {summary.days} days: {sessions_completed} sessions, {total_duration_hours:.1f} hours total"


def _format_training_history(activities: list[Activity], days: int = 7) -> str:
    """Format training history as a string.

    Args:
        activities: List of activities
        days: Number of days to include

    Returns:
        Formatted training history string
    """
    if not activities:
        return "No recent training history"

    since = datetime.now(timezone.utc) - timedelta(days=days)
    recent = [a for a in activities if _normalize_datetime(a.starts_at) >= since]

    if not recent:
        return f"No activities in the last {days} days"

    total_duration = sum(a.duration_seconds or 0 for a in recent) / 3600.0
    activity_count = len(recent)

    return f"Last {days} days: {activity_count} sessions, {total_duration:.1f} hours total"


def _get_yesterday_training_from_summary(summary) -> str:
    """Get description of yesterday's training from TrainingSummary.

    Args:
        summary: TrainingSummary object

    Returns:
        Description of yesterday's training
    """
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    yesterday_str = yesterday.isoformat()

    # Check key sessions for yesterday
    for key_session in summary.last_key_sessions:
        if key_session.date == yesterday_str:
            if key_session.status == "completed":
                return f"{key_session.title} completed"
            if key_session.status == "missed":
                return "Rest day"
            return f"{key_session.title} ({key_session.status})"

    # Check if there were any completed sessions yesterday
    # We'd need to look at reconciliation results, but for now return generic
    return "Rest day"


def _get_yesterday_training(activities: list[Activity]) -> str:
    """Get description of yesterday's training.

    Args:
        activities: List of activities

    Returns:
        Description of yesterday's training
    """
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    yesterday_start = datetime.combine(yesterday, datetime.min.time()).replace(tzinfo=timezone.utc)
    yesterday_end = datetime.combine(yesterday, datetime.max.time()).replace(tzinfo=timezone.utc)

    yesterday_activities = [a for a in activities if yesterday_start <= _normalize_datetime(a.starts_at) <= yesterday_end]

    if not yesterday_activities:
        return "Rest day"

    if len(yesterday_activities) == 1:
        activity = yesterday_activities[0]
        duration_min = (activity.duration_seconds or 0) // 60
        distance_km = (activity.distance_meters or 0) / 1000.0
        parts = [activity.type or "Activity"]
        if duration_min > 0:
            parts.append(f"{duration_min} min")
        if distance_km > 0:
            parts.append(f"{distance_km:.1f} km")
        return " • ".join(parts)

    return f"{len(yesterday_activities)} sessions completed"


def _get_activities_for_context(user_id: str) -> list[Activity]:
    """Get activities for context building.

    Args:
        user_id: User ID

    Returns:
        List of activities from last 14 days (detached from session)
    """
    with get_session() as session:
        since = datetime.now(timezone.utc) - timedelta(days=14)
        activities = (
            session.execute(
                select(Activity)
                .where(
                    Activity.user_id == user_id,
                    Activity.starts_at >= since,
                )
                .order_by(Activity.starts_at.desc())
            )
            .scalars()
            .all()
        )
        # Detach objects from session so they can be used after session closes
        activities_list = list(activities)
        for activity in activities_list:
            session.expunge(activity)
        return activities_list


def _get_weekly_intent_for_context(athlete_id: int, decision_date: date) -> WeeklyIntent | None:
    """Get weekly intent for context if available.

    Weekly intent is optional - if it's not available or if there's an error,
    this function returns None and the context will be built without it.

    Args:
        athlete_id: Athlete ID
        decision_date: Decision date

    Returns:
        WeeklyIntent if available, None otherwise
    """
    try:
        store = IntentStore()
        week_start = decision_date - timedelta(days=decision_date.weekday())
        week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
        weekly_intent_model = store.get_latest_weekly_intent(athlete_id, week_start_dt, active_only=True)
        if weekly_intent_model:
            try:
                return WeeklyIntent(**weekly_intent_model.intent_data)
            except Exception as e:
                logger.warning(f"Failed to parse weekly intent: {e}")
    except Exception as e:
        # Weekly intent is optional - log but don't fail if there's an error
        # This can happen if the athlete_id column doesn't exist yet (migration pending)
        logger.debug(f"Weekly intent not available (optional): {e}")
    return None


def _get_previous_week_intent(athlete_id: int, week_start: date) -> WeeklyIntent | None:
    """Get previous week's intent for comparison.

    Args:
        athlete_id: Athlete ID
        week_start: Current week start date

    Returns:
        Previous week's WeeklyIntent if available, None otherwise
    """
    store = IntentStore()
    previous_week_start = week_start - timedelta(days=7)
    previous_week_start_dt = datetime.combine(previous_week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    previous_intent_model = store.get_latest_weekly_intent(athlete_id, previous_week_start_dt, active_only=True)
    if previous_intent_model:
        try:
            return WeeklyIntent(**previous_intent_model.intent_data)
        except Exception as e:
            logger.warning(f"Failed to parse previous week intent: {e}")
    return None


def _get_recent_decisions_for_context(user_id: str, decision_date: date) -> list[DailyDecision]:
    """Get recent daily decisions for context.

    Args:
        user_id: User ID (schema v2: migrated from athlete_id)
        decision_date: Decision date

    Returns:
        List of recent daily decisions (last 3 days)
    """
    store = IntentStore()
    recent_decisions = []
    for days_ago in range(1, 4):
        check_date = decision_date - timedelta(days=days_ago)
        check_date_dt = datetime.combine(check_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        decision_model = store.get_latest_daily_decision(user_id, check_date_dt, active_only=True)
        if decision_model:
            try:
                decision = DailyDecision(**decision_model.decision_data)
                recent_decisions.append(decision)
            except Exception as e:
                logger.warning(f"Failed to parse recent decision: {e}")
    return recent_decisions


def _get_scheduled_workout_for_date(user_id: str, workout_date: date) -> dict[str, Any] | None:
    """Get scheduled workout for a specific date.

    Args:
        user_id: User ID
        workout_date: Date to check for scheduled workout

    Returns:
        Dictionary with workout details if found, None otherwise
    """
    try:
        with get_session() as session:
            # Get start and end of day in UTC
            day_start = datetime.combine(workout_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            day_end = day_start + timedelta(days=1)

            # Query for planned sessions on this date
            planned_sessions = (
                session.execute(
                    select(PlannedSession)
                    .where(
                        PlannedSession.user_id == user_id,
                        PlannedSession.starts_at >= day_start,
                        PlannedSession.starts_at < day_end,
                        PlannedSession.status == "planned",
                    )
                    .order_by(PlannedSession.starts_at.asc())
                )
                .scalars()
                .first()
            )

            if planned_sessions:
                return {
                    "id": planned_sessions.id,
                    "title": planned_sessions.title,
                    "sport": planned_sessions.sport,
                    "session_type": planned_sessions.session_type,
                    "duration_seconds": planned_sessions.duration_seconds,
                    "distance_meters": planned_sessions.distance_meters,
                    "intensity": planned_sessions.intensity,
                    "intent": planned_sessions.intent,
                    "notes": planned_sessions.notes,
                    "execution_notes": planned_sessions.execution_notes,
                }
    except Exception as e:
        logger.warning(f"Failed to get scheduled workout for date {workout_date}: {e}")
    return None


def build_daily_decision_context(
    user_id: str,
    athlete_id: int,
    decision_date: date,
) -> dict[str, Any]:
    """Build context dictionary for daily decision generation.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        decision_date: Date for the decision

    Returns:
        Context dictionary with:
        - athlete_state: Current athlete state
        - training_history: Recent training history string
        - yesterday_training: What was done yesterday
        - day_context: Day of week, time of year
        - weekly_intent: Optional weekly intent
        - recent_decisions: Optional recent daily decisions
    """
    logger.info(f"Building daily decision context for user_id={user_id}, athlete_id={athlete_id}, date={decision_date.isoformat()}")

    # Get overview data for athlete state
    try:
        overview = get_overview_data(user_id)
    except Exception as e:
        logger.warning(f"Failed to get overview data: {e}, using minimal context")
        overview = {"today": {}, "metrics": {"ctl": [], "atl": [], "tsb": []}, "data_quality": "insufficient"}

    # Get ALL activities for training history (not just matched ones)
    # This ensures the LLM sees complete activity history, not just activities matched to planned sessions
    activities_list = _get_activities_for_context(user_id)
    training_history = _format_training_history(activities_list, days=14)
    yesterday_training = _get_yesterday_training(activities_list)

    # Get training summary (canonical source) for other metrics (volume, compliance, etc.)
    # Note: This is only used for other context fields, not training_history
    try:
        build_training_summary(
            user_id=user_id,
            athlete_id=athlete_id,
            window_days=14,
        )
    except Exception as e:
        logger.warning(f"Failed to get training summary: {e!r}, continuing without summary metrics")

    athlete_state = _build_athlete_state_from_overview(overview)

    # Build day context
    decision_datetime = datetime.combine(decision_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_context = {
        "day_of_week": decision_datetime.strftime("%A"),
        "time_of_year": decision_datetime.strftime("%B"),
    }

    # Get optional context components
    weekly_intent = _get_weekly_intent_for_context(athlete_id, decision_date)
    recent_decisions = _get_recent_decisions_for_context(user_id, decision_date)

    # Get scheduled workout for today (if any)
    scheduled_workout = _get_scheduled_workout_for_date(user_id, decision_date)

    # Get reconciliation statistics (missed workouts, compliance)
    try:
        reconciliation_stats = get_reconciliation_stats(user_id=user_id, athlete_id=athlete_id, days=30)
        missed_workouts = get_recent_missed_workouts(user_id=user_id, athlete_id=athlete_id, days=14)
    except Exception as e:
        logger.warning(f"Failed to get reconciliation data: {e!r}, using defaults")
        reconciliation_stats = {
            "completed_count": 0,
            "missed_count": 0,
            "partial_count": 0,
            "substituted_count": 0,
            "skipped_count": 0,
            "total_planned": 0,
            "compliance_rate": 0.0,
        }
        missed_workouts = []

    # Build final context
    context = {
        "athlete_state": athlete_state,
        "training_history": training_history,
        "yesterday_training": yesterday_training,
        "day_context": day_context,
        "reconciliation": {
            "stats": reconciliation_stats,
            "recent_missed_workouts": missed_workouts,
        },
    }

    if weekly_intent:
        context["weekly_intent"] = weekly_intent.model_dump()

    if recent_decisions:
        context["recent_decisions"] = [d.model_dump() for d in recent_decisions]

    if scheduled_workout:
        context["scheduled_workout"] = scheduled_workout

    logger.info(
        f"Built context with athlete_state, training_history, weekly_intent={'present' if weekly_intent else 'none'}, "
        f"scheduled_workout={'present' if scheduled_workout else 'none'}, "
        f"compliance_rate={reconciliation_stats.get('compliance_rate', 0.0):.2f}"
    )
    return context


def build_weekly_intent_context(
    user_id: str,
    athlete_id: int,
    week_start: date,
) -> dict[str, Any]:
    """Build context dictionary for weekly intent generation.

    Args:
        user_id: User ID
        athlete_id: Athlete ID
        week_start: Week start date (Monday)

    Returns:
        Context dictionary with:
        - season_plan: Current SeasonPlan (if exists)
        - training_history: Recent training history (last 2-4 weeks)
        - athlete_state: Current athlete state
        - week_context: Week number, time of year, upcoming events
        - previous_week_intent: Previous week's intent (for comparison)
        - recent_decisions: Recent daily decisions
    """
    logger.info(f"Building weekly intent context for user_id={user_id}, athlete_id={athlete_id}, week_start={week_start.isoformat()}")

    # Get overview data for athlete state
    try:
        overview = get_overview_data(user_id)
    except Exception as e:
        logger.warning(f"Failed to get overview data: {e}, using minimal context")
        overview = {"today": {}, "metrics": {"ctl": [], "atl": [], "tsb": []}, "data_quality": "insufficient"}

    # Get training summary (canonical source)
    try:
        training_summary = build_training_summary(
            user_id=user_id,
            athlete_id=athlete_id,
            window_days=28,
        )
        training_history = _format_training_history_from_summary(training_summary)
    except Exception as e:
        logger.warning(f"Failed to get training summary: {e!r}, falling back to activities")
        activities_list = _get_activities_for_context(user_id)
        training_history = _format_training_history(activities_list, days=28)

    athlete_state = _build_athlete_state_from_overview(overview)

    # Build week context
    week_datetime = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    week_context = {
        "week_number": (week_start - date(2024, 1, 1)).days // 7 + 1,  # Simplified week number
        "time_of_year": week_datetime.strftime("%B"),
    }

    # Get optional context components
    store = IntentStore()
    season_plan_model = store.get_latest_season_plan(athlete_id, active_only=True)
    season_plan = None
    if season_plan_model:
        try:
            season_plan = SeasonPlan(**season_plan_model.plan_data)
        except Exception as e:
            logger.warning(f"Failed to parse season plan: {e}")

    previous_week_intent = _get_previous_week_intent(athlete_id, week_start)

    # Get reconciliation statistics (missed workouts, compliance)
    try:
        reconciliation_stats = get_reconciliation_stats(user_id=user_id, athlete_id=athlete_id, days=30)
        missed_workouts = get_recent_missed_workouts(user_id=user_id, athlete_id=athlete_id, days=14)
    except Exception as e:
        logger.warning(f"Failed to get reconciliation data: {e!r}, using defaults")
        reconciliation_stats = {
            "completed_count": 0,
            "missed_count": 0,
            "partial_count": 0,
            "substituted_count": 0,
            "skipped_count": 0,
            "total_planned": 0,
            "compliance_rate": 0.0,
        }
        missed_workouts = []

    # Get recent daily decisions (last 7 days)
    recent_decisions = []
    today = datetime.now(timezone.utc).date()
    for days_ago in range(0, 7):
        check_date = week_start + timedelta(days=days_ago)
        if check_date <= today:
            check_date_dt = datetime.combine(check_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            decision_model = store.get_latest_daily_decision(user_id, check_date_dt, active_only=True)
            if decision_model:
                try:
                    decision = DailyDecision(**decision_model.decision_data)
                    recent_decisions.append(decision)
                except Exception as e:
                    logger.warning(f"Failed to parse recent decision: {e}")

    # Build final context
    context = {
        "athlete_state": athlete_state,
        "training_history": training_history,
        "week_context": week_context,
        "reconciliation": {
            "stats": reconciliation_stats,
            "recent_missed_workouts": missed_workouts,
        },
    }

    if season_plan:
        context["season_plan"] = season_plan.model_dump()

    if previous_week_intent:
        context["previous_week_intent"] = previous_week_intent.model_dump()
        # Add change explanation context
        volume_change = previous_week_intent.volume_target_hours
        context["previous_volume"] = volume_change
        context["change_reasoning"] = {
            "previous_volume": previous_week_intent.volume_target_hours,
            "previous_focus": previous_week_intent.focus,
            "previous_risk_notes": previous_week_intent.risk_notes,
        }

    if recent_decisions:
        context["recent_decisions"] = [d.model_dump() for d in recent_decisions]

    logger.info(
        f"Built weekly intent context with season_plan={'present' if season_plan else 'none'}, "
        f"previous_week_intent={'present' if previous_week_intent else 'none'}"
    )
    return context
