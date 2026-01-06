from loguru import logger

from app.coach.schemas.athlete_state import AthleteState


def adjust_training_load(state: AthleteState, message: str) -> str:
    """Adjust training load based on athlete feedback.

    Args:
        state: Current athlete training state
        message: User's feedback or request about training load

    Returns:
        Training load adjustment data or clarification request
    """
    logger.info(f"Tool adjust_training_load called (message_length={len(message)}, TSB={state.tsb:.1f}, confidence={state.confidence:.2f})")

    if state.confidence < 0.1:
        return "[CLARIFICATION] athlete_state_confidence_low"

    # Return structured data for orchestrator to format
    return f"TSB: {state.tsb:.1f}, CTL: {state.ctl:.1f}, ATL: {state.atl:.1f}, Load trend: {state.load_trend}, Feedback: {message}"
