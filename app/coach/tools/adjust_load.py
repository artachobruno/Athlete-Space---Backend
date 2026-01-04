from loguru import logger

from app.coach.models import AthleteState


def adjust_training_load(state: AthleteState, message: str) -> str:
    """Adjust training load based on athlete feedback."""
    logger.info(f"Tool adjust_training_load called (message_length={len(message)}, TSB={state.tsb:.1f}, confidence={state.confidence:.2f})")

    # Check confidence - ask clarifying questions with insufficient data
    if state.confidence < 0.1:
        return (
            "I'd like to help adjust your training load! To give you the best recommendations, could you tell me:\n\n"
            "• How are you feeling? (tired, strong, or somewhere in between?)\n"
            "• What's your current training volume? (hours per week or sessions per week?)\n"
            "• What's your goal? (building fitness, maintaining, or recovering?)\n"
            "• Any specific concerns? (overtraining, undertraining, or just fine-tuning?)\n\n"
            "Based on your answers, I can suggest specific adjustments. "
            "Syncing your Strava activities will help me provide even more precise recommendations!"
        )

    tsb = state.tsb

    if "tired" in message.lower() or "fatigue" in message.lower():
        return (
            "Based on your feedback and current fatigue:\n\n"
            "🔻 Suggested adjustment:\n"
            "- Reduce weekly volume by 10-20%\n"
            "- Replace next quality session with easy aerobic\n"
            "- Reassess in 3 days"
        )

    if "good" in message.lower() or "strong" in message.lower():
        return (
            "You are responding well to training.\n\n"
            "🔺 Suggested adjustment:\n"
            "- Maintain current volume\n"
            "- You may add light intensity (strides or tempo blocks)"
        )

    if tsb < -12:
        return (
            "Objectively, fatigue is high.\n\n"
            "🔻 Recommended adjustment:\n"
            "- Immediate recovery day\n"
            "- Resume intensity only when TSB improves"
        )

    return "No major load adjustment needed.\n\n✔ Continue current structure and monitor fatigue markers."
