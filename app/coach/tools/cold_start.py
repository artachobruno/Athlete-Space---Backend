from app.coach.schemas.athlete_state import AthleteState


def welcome_new_user(state: AthleteState | None = None) -> str:
    """Generate a static welcome message for new users.

    Args:
        state: Optional athlete state. Currently unused, kept for compatibility.

    Returns:
        A welcoming message introducing the coach.
    """
    if state is not None and state.confidence >= 0.3:
        return "Hey! I see your training looks good. What can I help you with today?"
    return "Hi! What are you training for? I'd love to help you reach your goals."
