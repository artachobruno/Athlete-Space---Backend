"""Context builder for daily decision generation.

Builds structured context from athlete data for LLM-based daily decision generation.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.api.user.me import get_overview_data
from app.coach.schemas.intent_schemas import DailyDecision, WeeklyIntent
from app.db.models import Activity
from app.db.session import get_session
from app.services.intelligence.store import IntentStore


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
    recent = [a for a in activities if _normalize_datetime(a.start_time) >= since]

    if not recent:
        return f"No activities in the last {days} days"

    total_duration = sum(a.duration_seconds or 0 for a in recent) / 3600.0
    activity_count = len(recent)

    return f"Last {days} days: {activity_count} sessions, {total_duration:.1f} hours total"


def _get_yesterday_training(activities: list[Activity]) -> str:
    """Get description of yesterday's training.

    Args:
        activities: List of activities

    Returns:
        Description of yesterday's training
    """
    now = datetime.now(timezone.utc)
    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end = yesterday_start + timedelta(days=1)

    yesterday_activities = [a for a in activities if yesterday_start <= _normalize_datetime(a.start_time) < yesterday_end]

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
                    Activity.start_time >= since,
                )
                .order_by(Activity.start_time.desc())
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

    Args:
        athlete_id: Athlete ID
        decision_date: Decision date

    Returns:
        WeeklyIntent if available, None otherwise
    """
    store = IntentStore()
    week_start = decision_date - timedelta(days=decision_date.weekday())
    week_start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc)
    weekly_intent_model = store.get_latest_weekly_intent(athlete_id, week_start_dt, active_only=True)
    if weekly_intent_model:
        try:
            return WeeklyIntent(**weekly_intent_model.intent_data)
        except Exception as e:
            logger.warning(f"Failed to parse weekly intent: {e}")
    return None


def _get_recent_decisions_for_context(athlete_id: int, decision_date: date) -> list[DailyDecision]:
    """Get recent daily decisions for context.

    Args:
        athlete_id: Athlete ID
        decision_date: Decision date

    Returns:
        List of recent daily decisions (last 3 days)
    """
    store = IntentStore()
    recent_decisions = []
    for days_ago in range(1, 4):
        check_date = decision_date - timedelta(days=days_ago)
        check_date_dt = datetime.combine(check_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        decision_model = store.get_latest_daily_decision(athlete_id, check_date_dt, active_only=True)
        if decision_model:
            try:
                decision = DailyDecision(**decision_model.decision_data)
                recent_decisions.append(decision)
            except Exception as e:
                logger.warning(f"Failed to parse recent decision: {e}")
    return recent_decisions


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

    # Get activities and build context components
    activities_list = _get_activities_for_context(user_id)
    athlete_state = _build_athlete_state_from_overview(overview)
    training_history = _format_training_history(activities_list, days=7)
    yesterday_training = _get_yesterday_training(activities_list)

    # Build day context
    decision_datetime = datetime.combine(decision_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_context = {
        "day_of_week": decision_datetime.strftime("%A"),
        "time_of_year": decision_datetime.strftime("%B"),
    }

    # Get optional context components
    weekly_intent = _get_weekly_intent_for_context(athlete_id, decision_date)
    recent_decisions = _get_recent_decisions_for_context(athlete_id, decision_date)

    # Build final context
    context = {
        "athlete_state": athlete_state,
        "training_history": training_history,
        "yesterday_training": yesterday_training,
        "day_context": day_context,
    }

    if weekly_intent:
        context["weekly_intent"] = weekly_intent.model_dump()

    if recent_decisions:
        context["recent_decisions"] = [d.model_dump() for d in recent_decisions]

    logger.info(f"Built context with athlete_state, training_history, weekly_intent={'present' if weekly_intent else 'none'}")
    return context
