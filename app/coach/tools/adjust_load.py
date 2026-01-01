from loguru import logger

from app.coach.models import AthleteState


def adjust_training_load(state: AthleteState, message: str) -> str:
    """Adjust training load based on athlete feedback."""
    logger.info(f"Tool adjust_training_load called (message_length={len(message)}, TSB={state.tsb:.1f})")
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
