from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import select

from app.coach.schemas.athlete_state import AthleteState
from app.db.models import PlannedSession
from app.db.session import get_session

# Cache to prevent duplicate calls within a short time window
_recent_calls: dict[str, datetime] = {}


def _check_weekly_plan_exists(user_id: str | None, athlete_id: int | None) -> bool:
    """Check if planned sessions exist for the current week.

    Args:
        user_id: User ID (optional)
        athlete_id: Athlete ID (optional)

    Returns:
        True if planned sessions exist for current week, False otherwise
    """
    if user_id is None or athlete_id is None:
        return False

    try:
        now = datetime.now(timezone.utc)
        # Get Monday of current week
        days_since_monday = now.weekday()
        monday = (now - timedelta(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

        with get_session() as session:
            result = session.execute(
                select(PlannedSession)
                .where(
                    PlannedSession.user_id == user_id,
                    PlannedSession.athlete_id == athlete_id,
                    PlannedSession.date >= monday,
                    PlannedSession.date <= sunday,
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None
    except Exception as e:
        logger.warning(f"Error checking for existing weekly plan: {e}")
        return False


def plan_week(state: AthleteState, user_id: str | None = None, athlete_id: int | None = None) -> str:
    """Return training state data for weekly planning.

    Args:
        state: Current athlete state with load metrics and trends.
        user_id: User ID (optional, for idempotency check)
        athlete_id: Athlete ID (optional, for idempotency check)

    Returns:
        Training state data or clarification request
    """
    logger.info(f"Tool plan_week called (TSB={state.tsb:.1f}, load_trend={state.load_trend}, flags={state.flags})")

    # Idempotency check: if weekly plan already exists, return early
    if _check_weekly_plan_exists(user_id, athlete_id):
        logger.info("Weekly plan already exists for current week, returning early")
        return "Your weekly plan is already created."

    # Create a simple hash of the state for duplicate detection
    # Use key state values that would be the same for repeated calls
    state_hash = f"{state.tsb:.1f}_{state.load_trend}_{','.join(state.flags)}"
    now = datetime.now(timezone.utc)

    # Check if we've been called recently with similar state (within last 10 seconds)
    if state_hash in _recent_calls:
        last_time = _recent_calls[state_hash]
        if (now - last_time).total_seconds() < 10:
            logger.warning("Duplicate plan_week tool call detected within 10 seconds, blocking repeat call")
            return (
                "I've already provided weekly planning information. "
                "**Please do not call this tool again with the same state.**\n\n"
                "The current training state has already been analyzed. "
                "If you need different information, please ask a specific question."
            )

    # Update cache
    _recent_calls[state_hash] = now
    # Clean old entries (older than 30 seconds) to prevent memory growth
    cutoff = now - timedelta(seconds=30)
    keys_to_remove = [k for k, v in _recent_calls.items() if v <= cutoff]
    for key in keys_to_remove:
        del _recent_calls[key]

    if state.confidence < 0.1:
        return "[CLARIFICATION] athlete_state_confidence_low"

    # Return structured data for orchestrator to format
    state_data = (
        f"CTL: {state.ctl:.1f}, ATL: {state.atl:.1f}, TSB: {state.tsb:.1f}, "
        f"Load trend: {state.load_trend}, Volatility: {state.volatility}, "
        f"Days since rest: {state.days_since_rest}, "
        f"7-day volume: {state.seven_day_volume_hours:.1f}h, "
        f"14-day volume: {state.fourteen_day_volume_hours:.1f}h"
    )
    if state.flags:
        state_data += f", Flags: {', '.join(state.flags)}"
    if state.days_to_race:
        state_data += f", Days to race: {state.days_to_race}"
    return state_data
