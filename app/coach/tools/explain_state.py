from loguru import logger

from app.coach.models import AthleteState


def explain_training_state(state: AthleteState) -> str:
    """Explain current fitness, fatigue, and readiness in plain language."""
    logger.info(
        f"Tool explain_training_state called (CTL={state.ctl:.1f}, ATL={state.atl:.1f}, "
        f"TSB={state.tsb:.1f}, confidence={state.confidence:.2f})"
    )

    # Check confidence - ask clarifying questions with insufficient data
    if state.confidence < 0.1:
        return (
            "I'd love to explain your training state! To give you accurate insights, could you tell me:\n\n"
            "• How consistent has your training been? (daily, a few times per week, or irregular?)\n"
            "• What's your typical training volume? (hours per week?)\n"
            "• How are you feeling? (energetic, tired, or somewhere in between?)\n"
            "• What's your training goal right now? (building base, race prep, or maintaining?)\n\n"
            "Based on your answers, I can explain your current state and provide guidance. "
            "Syncing your Strava activities will help me provide even more detailed analysis!"
        )

    explanation = [
        f"Current CTL (fitness): {state.ctl:.1f}",
        f"Current ATL (fatigue): {state.atl:.1f}",
        f"Training Stress Balance (TSB): {state.tsb:.1f}",
        "",
    ]

    if state.tsb < -10:
        explanation.append(
            "You are carrying significant fatigue. Fitness is improving, but recovery needs attention to avoid overreaching."
        )
    elif state.tsb < 0:
        explanation.append("You are in a productive training zone. Fatigue is present but manageable.")
    else:
        explanation.append("You are well recovered. This is a good window for harder sessions or racing.")

    return "\n".join(explanation)
