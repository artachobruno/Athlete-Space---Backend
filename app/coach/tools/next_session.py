from datetime import datetime, timedelta, timezone

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr
from sqlalchemy import select

from app.coach.models import AthleteState
from app.core.settings import settings
from app.state.db import get_session
from app.state.models import Activity


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
    duration_min = activity.duration_seconds // 60
    distance_km = activity.distance_meters / 1000.0
    activity_type = activity.type

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
        total_duration = sum(a.duration_seconds for a in recent_activities) / 3600.0
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


def _generate_llm_recommendation(state: AthleteState, context_string: str) -> str:
    """Generate session recommendation using LLM.

    Args:
        state: Current athlete training state
        context_string: Context string about recent training history

    Returns:
        Recommendation string from LLM
    """
    system_prompt = """You are Virtus Coach, an elite endurance training intelligence system.

Your role is to recommend today's training session based on the athlete's current training state and recent training history.

Consider:
- Training Stress Balance (TSB): Negative values indicate fatigue, positive values indicate freshness
- Chronic Training Load (CTL): Long-term fitness level
- Acute Training Load (ATL): Recent training load
- Load trends: Are they building, maintaining, or recovering?
- Recent training history: What have they been doing?
- Days since rest: How long since their last rest day?
- Confidence: How much data is available?

Provide a clear, actionable recommendation that includes:
1. Brief context about their current state (if relevant)
2. A specific session recommendation with:
   - Session type (easy run, tempo, intervals, rest, etc.)
   - Duration or structure
   - Intensity guidance
   - Any specific considerations

Be concise, practical, and coach-like. Avoid explaining metrics - just provide the recommendation."""

    athlete_state_str = _build_athlete_state_string(state)

    user_prompt = athlete_state_str
    if context_string:
        user_prompt += f"\nRecent Training History:\n{context_string}\n"
    user_prompt += "\nRecommend today's training session:"

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.3,
        api_key=SecretStr(settings.openai_api_key),
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", user_prompt),
    ])

    chain = prompt | llm
    result = chain.invoke({})

    # Extract content from LLM response
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, str):
            return content
        return str(content)
    return str(result)


def recommend_next_session(state: AthleteState, user_id: str | None = None) -> str:
    """Recommend today's session based on fatigue, load balance, and historical data using LLM.

    Args:
        state: Current athlete training state
        user_id: Optional user ID to query historical activities

    Returns:
        Recommendation string with session details
    """
    logger.info(
        f"Tool recommend_next_session called (TSB={state.tsb:.1f}, CTL={state.ctl:.1f}, "
        f"confidence={state.confidence:.2f}, user_id={'provided' if user_id else 'not provided'})"
    )

    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY not set, cannot generate session recommendation with LLM")
        return (
            "I'd love to recommend today's session! To provide the best guidance, please ensure "
            "OpenAI API key is configured. Syncing your Strava activities will also help me provide "
            "personalized recommendations."
        )

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
        recommendation = _generate_llm_recommendation(state, context_string)
        logger.info("Generated session recommendation using LLM")
    except Exception as e:
        logger.error(f"Error generating session recommendation with LLM: {e}", exc_info=True)
        return (
            "I encountered an error generating your session recommendation. "
            "Please try again or ask a more specific question about your training."
        )
    else:
        return recommendation
