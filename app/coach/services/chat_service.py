from loguru import logger

from app.coach.schemas.athlete_state import AthleteState
from app.coach.services.state_builder import build_athlete_state
from app.coach.tools.cold_start import welcome_new_user
from app.state.api_helpers import get_training_data, get_user_id_from_athlete_id


def _handle_cold_start(athlete_id: int, days: int, days_to_race: int | None) -> tuple[str, str]:
    """Handle cold start scenario - provide welcome message."""
    logger.info(f"Cold start detected - providing welcome message (days={days}, days_to_race={days_to_race})")
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.warning(f"Cannot find user_id for athlete_id={athlete_id} in cold start")
        return ("error", "Unable to find user account. Please reconnect your Strava account.")
    try:
        logger.info("Fetching training data for cold start")
        training_data = get_training_data(user_id=user_id, days=days)
        logger.info("Building athlete state for cold start")
        athlete_state = build_athlete_state(
            ctl=training_data.ctl,
            atl=training_data.atl,
            tsb=training_data.tsb,
            daily_load=training_data.daily_load,
            days_to_race=days_to_race,
        )
        logger.info("Calling welcome_new_user tool")
        reply = welcome_new_user(athlete_state)
    except RuntimeError:
        # Even if we don't have training data, provide a welcome message
        logger.warning("Cold start with no training data available")
        reply = welcome_new_user(None)

    return ("cold_start", reply)


def _get_athlete_state(athlete_id: int, days: int, days_to_race: int | None) -> tuple[str, str] | tuple[None, AthleteState]:
    """Get athlete state or return error response.

    Returns:
        Tuple of (error_type, error_message) if error, or (None, AthleteState) if successful
    """
    logger.info(f"Getting athlete state (days={days}, days_to_race={days_to_race})")
    user_id = get_user_id_from_athlete_id(athlete_id)
    if user_id is None:
        logger.warning(f"Cannot find user_id for athlete_id={athlete_id}")
        return ("error", "Unable to find user account. Please reconnect your Strava account.")
    try:
        logger.info("Fetching training data")
        training_data = get_training_data(user_id=user_id, days=days)
    except RuntimeError as e:
        logger.warning(f"No training data available: {e}")
        # Return a special error type that allows LLM fallback
        return ("no_training_data", "")

    logger.info("Building athlete state from training data")
    athlete_state = build_athlete_state(
        ctl=training_data.ctl,
        atl=training_data.atl,
        tsb=training_data.tsb,
        daily_load=training_data.daily_load,
        days_to_race=days_to_race,
    )
    logger.info(f"Athlete state built successfully (CTL={athlete_state.ctl:.1f}, ATL={athlete_state.atl:.1f}, TSB={athlete_state.tsb:.1f})")
    return (None, athlete_state)


def dispatch_coach_chat(
    message: str,
    athlete_id: int,
    days: int,
    days_to_race: int | None = None,
    *,
    history_empty: bool = False,
    conversation_history: list[dict[str, str]] | None = None,
    use_orchestrator: bool = True,
) -> tuple[str, str]:
    """Route user message -> coaching tool -> response text.

    Args:
        message: User's message to the coach
        athlete_id: Strava athlete ID (int)
        days: Number of days of training data to consider
        days_to_race: Optional days until race
        history_empty: If True, this is a cold start (first message).
                      Will return welcome message instead of routing intent.
        conversation_history: List of previous messages with 'role' and 'content' keys.
                             Used to provide context to the LLM.
        use_orchestrator: Deprecated parameter, kept for backward compatibility.
                         All requests now use intent-based routing.
    """
    history_count = len(conversation_history) if conversation_history else 0
    logger.info(
        f"Dispatching coach chat (message_length={len(message)}, days={days}, "
        f"days_to_race={days_to_race}, history_empty={history_empty}, "
        f"history_count={history_count}, use_orchestrator={use_orchestrator})"
    )

    # Handle cold start - provide welcome message regardless of intent
    if history_empty:
        logger.info("Handling cold start scenario")
        return _handle_cold_start(athlete_id, days, days_to_race)

    # Build athlete state
    logger.info("Building athlete state")
    state_result = _get_athlete_state(athlete_id, days, days_to_race)
    has_training_data = state_result[0] is None

    if not has_training_data:  # Error response
        error_type = state_result[0]
        logger.warning(f"Failed to get athlete state: {error_type}")
        # Return error message - orchestrator should handle this
        return state_result  # type: ignore[return-value]

    # At this point, state_result[0] is None, so state_result[1] is AthleteState
    athlete_state = state_result[1]

    # Return state for orchestrator to handle
    logger.info("Returning state for orchestrator handling")
    return (
        "orchestrator",
        f"Athlete state available: CTL={athlete_state.ctl:.1f}, ATL={athlete_state.atl:.1f}, TSB={athlete_state.tsb:.1f}",
    )
