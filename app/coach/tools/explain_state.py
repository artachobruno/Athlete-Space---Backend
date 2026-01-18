from loguru import logger

from app.coach.schemas.athlete_state import AthleteState


def explain_training_state(state: AthleteState) -> str:
    """Return conversational training state explanation.

    Args:
        state: Current athlete training state

    Returns:
        Conversational explanation of training state
    """
    logger.info(
        f"Tool explain_training_state called (CTL={state.ctl:.1f}, ATL={state.atl:.1f}, "
        f"TSB={state.tsb:.1f}, confidence={state.confidence:.2f})"
    )

    if state.confidence < 0.1:
        return "[CLARIFICATION] athlete_state_confidence_low"

    # Build conversational response
    parts = []

    # Start with main metrics in natural language
    parts.append(f"Your CTL is {state.ctl:.1f}, ATL is {state.atl:.1f}, and TSB is {state.tsb:.1f}.")

    # Interpret load trend
    if state.load_trend == "rising":
        parts.append("Load is building steadily.")
    elif state.load_trend == "falling":
        parts.append("Load is decreasing, which suggests good recovery.")
    elif state.load_trend == "stable":
        parts.append("Load is holding steady.")

    # Add context about recovery/fatigue based on TSB
    if state.tsb > 10:
        parts.append("You're well-recovered and ready for quality work.")
    elif state.tsb < -10:
        parts.append("You're carrying some fatigue—consider prioritizing recovery.")
    elif state.tsb < -20:
        parts.append("You're in a fatigued state; rest may be needed soon.")

    # Add volume context if meaningful
    if state.seven_day_volume_hours > 0:
        parts.append(f"Volume this week: {state.seven_day_volume_hours:.1f} hours.")

    # Add days since rest if significant
    if state.days_since_rest > 10:
        parts.append(f"It's been {state.days_since_rest} days since your last rest day—consider scheduling one soon.")

    # Add race context if applicable
    if state.days_to_race:
        parts.append(f"You're {state.days_to_race} days out from your race.")

    # Join parts with natural flow
    return " ".join(parts)
