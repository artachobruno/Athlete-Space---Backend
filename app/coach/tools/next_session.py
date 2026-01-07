from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.coach.schemas.athlete_state import AthleteState
from app.coach.utils.llm_client import CoachLLMClient
from app.db.models import Activity
from app.db.session import get_session


def _get_recent_activities(user_id: str, days: int = 7) -> list[Activity]:
    """Get recent activities for a user.

    Args:
        user_id: User ID (Clerk string)
        days: Number of days to look back (default: 7)

    Returns:
        List of Activity objects, ordered by start_time (most recent first)
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as session:
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
        return list(activities)


def _get_yesterday_activities(user_id: str) -> list[Activity]:
    """Get activities from yesterday.

    Args:
        user_id: User ID (Clerk string)

    Returns:
        List of Activity objects from yesterday
    """
    now = datetime.now(timezone.utc)
    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end = yesterday_start + timedelta(days=1)

    with get_session() as session:
        activities = (
            session.execute(
                select(Activity)
                .where(
                    Activity.user_id == user_id,
                    Activity.start_time >= yesterday_start,
                    Activity.start_time < yesterday_end,
                )
                .order_by(Activity.start_time.desc())
            )
            .scalars()
            .all()
        )
        return list(activities)


def _format_activity_summary(activity: Activity) -> str:
    """Format a single activity for display.

    Args:
        activity: Activity object

    Returns:
        Formatted string describing the activity
    """
    if activity.duration_seconds is None:
        duration_min = 0
    else:
        duration_min = activity.duration_seconds // 60

    if activity.distance_meters is None:
        distance_km = 0.0
    else:
        distance_km = activity.distance_meters / 1000.0

    activity_type = activity.type or "Activity"

    parts = [f"{activity_type}"]
    if duration_min > 0:
        parts.append(f"{duration_min} min")
    if distance_km > 0:
        parts.append(f"{distance_km:.1f} km")
    return " • ".join(parts)


def _build_context_string(yesterday_activities: list[Activity], recent_activities: list[Activity]) -> str:
    """Build context string about recent training history.

    Args:
        yesterday_activities: List of activities from yesterday
        recent_activities: List of recent activities (last 7 days)

    Returns:
        Context string about recent training
    """
    context_parts = []

    # Yesterday's activities
    if yesterday_activities:
        yesterday_summaries = [_format_activity_summary(a) for a in yesterday_activities]
        if len(yesterday_summaries) == 1:
            context_parts.append(f"Yesterday: {yesterday_summaries[0]}")
        else:
            context_parts.append(f"Yesterday: {len(yesterday_activities)} sessions ({', '.join(yesterday_summaries[:2])})")
    elif recent_activities:
        days_ago = (datetime.now(timezone.utc) - recent_activities[0].start_time).days
        if days_ago == 0:
            context_parts.append("Today: Already completed activity")
        elif days_ago == 1:
            context_parts.append("Yesterday: Rest day")
        else:
            context_parts.append(f"Last activity: {days_ago} days ago")

    # Recent pattern
    if recent_activities:
        total_duration_seconds = sum((a.duration_seconds or 0) for a in recent_activities)
        total_duration = total_duration_seconds / 3600.0
        activity_count = len(recent_activities)
        context_parts.append(f"Last 7 days: {activity_count} sessions, {total_duration:.1f} hours")

    return " | ".join(context_parts) if context_parts else "No recent activities found"


def _build_athlete_state_string(state: AthleteState) -> str:
    """Build formatted string representation of athlete state.

    Args:
        state: Current athlete training state

    Returns:
        Formatted string with athlete state information
    """
    athlete_state_str = (
        f"Training State:\n"
        f"- CTL (fitness): {state.ctl:.1f}\n"
        f"- ATL (fatigue): {state.atl:.1f}\n"
        f"- TSB (balance): {state.tsb:.1f}\n"
        f"- Load trend: {state.load_trend}\n"
        f"- Volatility: {state.volatility}\n"
        f"- Days since rest: {state.days_since_rest}\n"
        f"- 7-day volume: {state.seven_day_volume_hours:.1f} hours\n"
        f"- 14-day volume: {state.fourteen_day_volume_hours:.1f} hours\n"
        f"- Confidence: {state.confidence:.2f}\n"
    )

    if state.flags:
        athlete_state_str += f"- Flags: {', '.join(state.flags)}\n"

    if state.days_to_race:
        athlete_state_str += f"- Days to race: {state.days_to_race}\n"

    return athlete_state_str


def _generate_daily_decision_recommendation(state: AthleteState, context_string: str) -> str:
    """Generate session recommendation using daily_decision via llm_client.

    Args:
        state: Current athlete training state
        context_string: Context string about recent training history

    Returns:
        Recommendation string from daily_decision
    """
    try:
        client = CoachLLMClient()

        # Build context for daily_decision
        context: dict[str, Any] = {
            "athlete_state": state.model_dump(),
            "training_history": context_string if context_string else "No recent training history",
            "yesterday_training": context_string.split(" | ", maxsplit=1)[0] if context_string and " | " in context_string else "Unknown",
            "day_context": {
                "day_of_week": datetime.now(timezone.utc).strftime("%A"),
                "time_of_year": datetime.now(timezone.utc).strftime("%B"),
            },
        }

        decision = client.generate_daily_decision(context)

        # Format the decision as a recommendation string
        recommendation_parts = []
        if decision.explanation:
            recommendation_parts.append(decision.explanation)
        if decision.session_type:
            recommendation_parts.append(f"Session type: {decision.session_type}")
        if decision.volume_hours:
            recommendation_parts.append(f"Duration: {decision.volume_hours:.1f} hours")
        if decision.intensity_focus:
            recommendation_parts.append(f"Intensity: {decision.intensity_focus}")

        return "\n".join(recommendation_parts) if recommendation_parts else decision.explanation or "No recommendation available"
    except Exception as e:
        logger.error(f"Error generating daily decision: {e}", exc_info=True)
        return "[CLARIFICATION] daily_decision_generation_failed"


def recommend_next_session(state: AthleteState, user_id: str | None = None) -> str:
    """Recommend today's session using daily_decision via llm_client.

    Args:
        state: Current athlete training state
        user_id: Optional user ID to query historical activities

    Returns:
        Recommendation string with session details or clarification request
    """
    logger.info(
        f"Tool recommend_next_session called (TSB={state.tsb:.1f}, CTL={state.ctl:.1f}, "
        f"confidence={state.confidence:.2f}, user_id={'provided' if user_id else 'not provided'})"
    )

    if state.confidence < 0.1:
        return "[CLARIFICATION] athlete_state_confidence_low"

    # Get historical context if user_id is available
    context_string = ""
    if user_id:
        try:
            yesterday_activities = _get_yesterday_activities(user_id)
            recent_activities = _get_recent_activities(user_id, days=7)
            context_string = _build_context_string(yesterday_activities, recent_activities)
            logger.info(f"Found {len(yesterday_activities)} activities yesterday, {len(recent_activities)} activities in last 7 days")
        except Exception as e:
            logger.warning(f"Error fetching activities: {e}, proceeding without historical context")

    try:
        recommendation = _generate_daily_decision_recommendation(state, context_string)
        logger.info("Generated session recommendation using daily_decision")
    except Exception as e:
        logger.error(f"Error generating session recommendation: {e}", exc_info=True)
        return "[CLARIFICATION] daily_decision_generation_failed"
    else:
        return recommendation
